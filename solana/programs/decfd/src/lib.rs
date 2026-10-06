//! DeCFD on-chain program: jobs, tasks, miner stakes, audits, slashing and payouts.
//!
//! The simulations run off-chain. The chain holds the money and the commitments:
//! a client escrows a job budget, every simulation task reserves a reward, miners stake to
//! join and commit to their results, a verifier re-runs a sample and slashes mismatches,
//! and settled tasks pay the miner. Mirrors orchestrator_py/network/ledger.py.
use anchor_lang::prelude::*;
use anchor_lang::system_program;

// Solana Playground replaces this with the program's own id on the first build.
declare_id!("11111111111111111111111111111111");

pub const MINER_ACTIVE: u8 = 0;
pub const MINER_BANNED: u8 = 1;

pub const TASK_OPEN: u8 = 0;
pub const TASK_SUBMITTED: u8 = 1;
pub const TASK_VERIFIED: u8 = 2;
pub const TASK_REJECTED: u8 = 3;
pub const TASK_FINALIZED: u8 = 4;
pub const TASK_CANCELLED: u8 = 5;

pub const JOB_OPEN: u8 = 0;
pub const JOB_CLOSED: u8 = 1;

pub const MAX_NAME_LEN: usize = 16;

#[program]
pub mod decfd {
    use super::*;

    /// Create a network instance: who verifies, how much stake is required, how hard to slash.
    pub fn initialize(
        ctx: Context<Initialize>,
        network_id: u64,
        verifier: Pubkey,
        min_stake: u64,
        slash_bps: u16,
        verifier_share_bps: u16,
        challenge_window: i64,
    ) -> Result<()> {
        require!(slash_bps <= 10_000 && verifier_share_bps <= 10_000 && challenge_window >= 0,
                 DecfdError::BadParams);
        let c = &mut ctx.accounts.config;
        c.admin = ctx.accounts.admin.key();
        c.network_id = network_id;
        c.verifier = verifier;
        c.min_stake = min_stake;
        c.slash_bps = slash_bps;
        c.verifier_share_bps = verifier_share_bps;
        c.challenge_window = challenge_window;
        c.treasury = 0;
        c.bump = ctx.bumps.config;
        Ok(())
    }

    /// A miner joins the network by locking its stake in its miner account.
    pub fn register_miner(ctx: Context<RegisterMiner>, name: String, stake: u64) -> Result<()> {
        require!(name.len() <= MAX_NAME_LEN, DecfdError::NameTooLong);
        require!(stake >= ctx.accounts.config.min_stake, DecfdError::StakeTooLow);
        system_program::transfer(
            CpiContext::new(
                ctx.accounts.system_program.to_account_info(),
                system_program::Transfer {
                    from: ctx.accounts.authority.to_account_info(),
                    to: ctx.accounts.miner.to_account_info(),
                },
            ),
            stake,
        )?;
        let m = &mut ctx.accounts.miner;
        m.config = ctx.accounts.config.key();
        m.authority = ctx.accounts.authority.key();
        m.stake = stake;
        m.earned = 0;
        m.slashed = 0;
        m.tasks = 0;
        m.caught = 0;
        m.status = MINER_ACTIVE;
        m.bump = ctx.bumps.miner;
        m.name = name;
        Ok(())
    }

    /// The client opens a job and moves its whole budget into the job's escrow.
    pub fn create_job(
        ctx: Context<CreateJob>,
        job_id: u64,
        budget: u64,
        reward: u64,
        binary_hash: [u8; 32],
        steps: u32,
        avg: u32,
    ) -> Result<()> {
        require!(reward > 0 && budget >= reward, DecfdError::BadParams);
        system_program::transfer(
            CpiContext::new(
                ctx.accounts.system_program.to_account_info(),
                system_program::Transfer {
                    from: ctx.accounts.client.to_account_info(),
                    to: ctx.accounts.job.to_account_info(),
                },
            ),
            budget,
        )?;
        let j = &mut ctx.accounts.job;
        j.config = ctx.accounts.config.key();
        j.client = ctx.accounts.client.key();
        j.job_id = job_id;
        j.budget = budget;
        j.escrow = budget;
        j.reserved = 0;
        j.reward = reward;
        j.binary_hash = binary_hash;
        j.steps = steps;
        j.avg = avg;
        j.tasks = 0;
        j.status = JOB_OPEN;
        j.bump = ctx.bumps.job;
        Ok(())
    }

    /// One simulation task: the shape parameters, who should compute it, one reserved reward.
    pub fn create_task(
        ctx: Context<CreateTask>,
        index: u32,
        epoch: u32,
        params: [f64; 8],
        params_hash: [u8; 32],
        assigned: Pubkey,
    ) -> Result<()> {
        let j = &mut ctx.accounts.job;
        require!(j.status == JOB_OPEN, DecfdError::JobNotOpen);
        let free = j.escrow.checked_sub(j.reserved).ok_or(DecfdError::Overflow)?;
        require!(free >= j.reward, DecfdError::BudgetExhausted);
        j.reserved = j.reserved.checked_add(j.reward).ok_or(DecfdError::Overflow)?;
        j.tasks += 1;

        let t = &mut ctx.accounts.task;
        t.job = j.key();
        t.assigned = assigned;
        t.status = TASK_OPEN;
        t.index = index;
        t.epoch = epoch;
        t.params = params;
        t.params_hash = params_hash;
        t.reward = j.reward;
        t.miner = Pubkey::default();
        t.result = [0.0; 4];
        t.result_hash = [0; 32];
        t.submitted_at = 0;
        t.bump = ctx.bumps.task;
        Ok(())
    }

    /// The assigned miner publishes its result and the hash it commits to.
    pub fn submit_result(ctx: Context<SubmitResult>, result: [f64; 4], result_hash: [u8; 32]) -> Result<()> {
        let m = &mut ctx.accounts.miner;
        let t = &mut ctx.accounts.task;
        require!(t.status == TASK_OPEN, DecfdError::TaskNotOpen);
        require!(m.status == MINER_ACTIVE, DecfdError::MinerNotActive);
        let who = ctx.accounts.authority.key();
        require!(t.assigned == Pubkey::default() || t.assigned == who, DecfdError::NotAssigned);
        t.miner = who;
        t.result = result;
        t.result_hash = result_hash;
        t.status = TASK_SUBMITTED;
        t.submitted_at = Clock::get()?.unix_timestamp;
        m.tasks += 1;
        Ok(())
    }

    /// The verifier re-ran the task. Match: verified. Mismatch: slash the miner, release the reward.
    pub fn resolve_challenge(ctx: Context<ResolveChallenge>, verifier_hash: [u8; 32]) -> Result<()> {
        let t = &mut ctx.accounts.task;
        require!(t.status == TASK_SUBMITTED, DecfdError::TaskNotSubmitted);
        if verifier_hash == t.result_hash {
            t.status = TASK_VERIFIED;
            return Ok(());
        }

        let c = &mut ctx.accounts.config;
        let m = &mut ctx.accounts.miner;
        let penalty = (m.stake as u128 * c.slash_bps as u128 / 10_000) as u64;
        let to_verifier = (penalty as u128 * c.verifier_share_bps as u128 / 10_000) as u64;
        let to_treasury = penalty - to_verifier;
        move_lamports(&m.to_account_info(), &ctx.accounts.verifier.to_account_info(), to_verifier)?;
        move_lamports(&m.to_account_info(), &c.to_account_info(), to_treasury)?;

        m.stake -= penalty;
        m.slashed += penalty;
        m.caught += 1;
        if m.stake < c.min_stake {
            m.status = MINER_BANNED;
        }
        c.treasury += to_treasury;
        let j = &mut ctx.accounts.job;
        j.reserved = j.reserved.checked_sub(t.reward).ok_or(DecfdError::Overflow)?;
        t.status = TASK_REJECTED;
        msg!("slashed {} lamports ({} to the verifier), banned: {}", penalty, to_verifier,
             m.status == MINER_BANNED);
        Ok(())
    }

    /// Pay a task: anyone may call it once the task is verified or its challenge window passed.
    pub fn settle_task(ctx: Context<SettleTask>) -> Result<()> {
        let t = &mut ctx.accounts.task;
        let now = Clock::get()?.unix_timestamp;
        let window_over = t.status == TASK_SUBMITTED
            && now >= t.submitted_at + ctx.accounts.config.challenge_window;
        require!(t.status == TASK_VERIFIED || window_over, DecfdError::NotSettleable);

        let j = &mut ctx.accounts.job;
        move_lamports(&j.to_account_info(), &ctx.accounts.miner_wallet.to_account_info(), t.reward)?;
        j.escrow = j.escrow.checked_sub(t.reward).ok_or(DecfdError::Overflow)?;
        j.reserved = j.reserved.checked_sub(t.reward).ok_or(DecfdError::Overflow)?;
        ctx.accounts.miner.earned += t.reward;
        t.status = TASK_FINALIZED;
        Ok(())
    }

    /// The client withdraws a task nobody computed (e.g. a remote miner went offline).
    pub fn cancel_task(ctx: Context<CancelTask>) -> Result<()> {
        let t = &mut ctx.accounts.task;
        require!(t.status == TASK_OPEN, DecfdError::TaskNotOpen);
        let j = &mut ctx.accounts.job;
        j.reserved = j.reserved.checked_sub(t.reward).ok_or(DecfdError::Overflow)?;
        t.status = TASK_CANCELLED;
        Ok(())
    }

    /// End of the run: the unreserved escrow returns to the client.
    pub fn close_job(ctx: Context<CloseJob>) -> Result<()> {
        let j = &mut ctx.accounts.job;
        require!(j.status == JOB_OPEN, DecfdError::JobNotOpen);
        require!(j.reserved == 0, DecfdError::UnsettledTasks);
        let refund = j.escrow;
        move_lamports(&j.to_account_info(), &ctx.accounts.client.to_account_info(), refund)?;
        j.escrow = 0;
        j.status = JOB_CLOSED;
        Ok(())
    }
}

/// Move lamports out of an account this program owns (miner stake, job escrow).
fn move_lamports(from: &AccountInfo, to: &AccountInfo, amount: u64) -> Result<()> {
    if amount == 0 {
        return Ok(());
    }
    let from_balance = from.lamports();
    let to_balance = to.lamports();
    **from.try_borrow_mut_lamports()? = from_balance.checked_sub(amount).ok_or(DecfdError::Overflow)?;
    **to.try_borrow_mut_lamports()? = to_balance.checked_add(amount).ok_or(DecfdError::Overflow)?;
    Ok(())
}

// ---------------------------------------------------------------------------------- accounts

#[account]
#[derive(InitSpace)]
pub struct Config {
    pub admin: Pubkey,
    pub verifier: Pubkey,
    pub network_id: u64,
    pub min_stake: u64,
    pub challenge_window: i64,
    pub treasury: u64,
    pub slash_bps: u16,
    pub verifier_share_bps: u16,
    pub bump: u8,
}

#[account]
#[derive(InitSpace)]
pub struct Miner {
    pub config: Pubkey,
    pub authority: Pubkey,
    pub stake: u64,
    pub earned: u64,
    pub slashed: u64,
    pub tasks: u32,
    pub caught: u32,
    pub status: u8,
    pub bump: u8,
    #[max_len(16)]
    pub name: String,
}

#[account]
#[derive(InitSpace)]
pub struct Job {
    pub config: Pubkey,
    pub client: Pubkey,
    pub job_id: u64,
    pub budget: u64,
    pub escrow: u64,
    pub reserved: u64,
    pub reward: u64,
    pub binary_hash: [u8; 32],
    pub steps: u32,
    pub avg: u32,
    pub tasks: u32,
    pub status: u8,
    pub bump: u8,
}

/// Fixed layout (no strings), so miners can filter tasks by job, assignee and status.
#[account]
#[derive(InitSpace)]
pub struct Task {
    pub job: Pubkey,          // offset 8
    pub assigned: Pubkey,     // offset 40
    pub status: u8,           // offset 72
    pub index: u32,
    pub epoch: u32,
    pub params: [f64; 8],     // L, t0..t4, alpha, camber
    pub params_hash: [u8; 32],
    pub reward: u64,
    pub miner: Pubkey,
    pub result: [f64; 4],     // fx, fy, cd, cl
    pub result_hash: [u8; 32],
    pub submitted_at: i64,
    pub bump: u8,
}

// ---------------------------------------------------------------------------------- contexts

#[derive(Accounts)]
#[instruction(network_id: u64)]
pub struct Initialize<'info> {
    #[account(init, payer = admin, space = 8 + Config::INIT_SPACE,
              seeds = [b"config", admin.key().as_ref(), &network_id.to_le_bytes()], bump)]
    pub config: Account<'info, Config>,
    #[account(mut)]
    pub admin: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct RegisterMiner<'info> {
    pub config: Account<'info, Config>,
    #[account(init, payer = authority, space = 8 + Miner::INIT_SPACE,
              seeds = [b"miner", config.key().as_ref(), authority.key().as_ref()], bump)]
    pub miner: Account<'info, Miner>,
    #[account(mut)]
    pub authority: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(job_id: u64)]
pub struct CreateJob<'info> {
    pub config: Account<'info, Config>,
    #[account(init, payer = client, space = 8 + Job::INIT_SPACE,
              seeds = [b"job", config.key().as_ref(), &job_id.to_le_bytes()], bump)]
    pub job: Account<'info, Job>,
    #[account(mut)]
    pub client: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(index: u32)]
pub struct CreateTask<'info> {
    #[account(mut, has_one = client)]
    pub job: Account<'info, Job>,
    #[account(init, payer = client, space = 8 + Task::INIT_SPACE,
              seeds = [b"task", job.key().as_ref(), &index.to_le_bytes()], bump)]
    pub task: Account<'info, Task>,
    #[account(mut)]
    pub client: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct SubmitResult<'info> {
    pub config: Account<'info, Config>,
    #[account(has_one = config)]
    pub job: Account<'info, Job>,
    #[account(mut, has_one = job)]
    pub task: Account<'info, Task>,
    #[account(mut, has_one = config,
              seeds = [b"miner", config.key().as_ref(), authority.key().as_ref()], bump = miner.bump)]
    pub miner: Account<'info, Miner>,
    pub authority: Signer<'info>,
}

#[derive(Accounts)]
pub struct ResolveChallenge<'info> {
    #[account(mut, has_one = verifier)]
    pub config: Account<'info, Config>,
    #[account(mut, has_one = config)]
    pub job: Account<'info, Job>,
    #[account(mut, has_one = job)]
    pub task: Account<'info, Task>,
    #[account(mut, seeds = [b"miner", config.key().as_ref(), task.miner.as_ref()], bump = miner.bump)]
    pub miner: Account<'info, Miner>,
    #[account(mut)]
    pub verifier: Signer<'info>,
}

#[derive(Accounts)]
pub struct SettleTask<'info> {
    pub config: Account<'info, Config>,
    #[account(mut, has_one = config)]
    pub job: Account<'info, Job>,
    #[account(mut, has_one = job)]
    pub task: Account<'info, Task>,
    #[account(mut, seeds = [b"miner", config.key().as_ref(), task.miner.as_ref()], bump = miner.bump)]
    pub miner: Account<'info, Miner>,
    /// CHECK: only receives the reward; must be the wallet that submitted the task.
    #[account(mut, address = task.miner)]
    pub miner_wallet: UncheckedAccount<'info>,
    pub cranker: Signer<'info>,
}

#[derive(Accounts)]
pub struct CancelTask<'info> {
    #[account(mut, has_one = client)]
    pub job: Account<'info, Job>,
    #[account(mut, has_one = job)]
    pub task: Account<'info, Task>,
    pub client: Signer<'info>,
}

#[derive(Accounts)]
pub struct CloseJob<'info> {
    #[account(mut, has_one = client)]
    pub job: Account<'info, Job>,
    #[account(mut)]
    pub client: Signer<'info>,
}

#[error_code]
pub enum DecfdError {
    #[msg("Invalid parameters")]
    BadParams,
    #[msg("Stake is below the network minimum")]
    StakeTooLow,
    #[msg("Miner name is longer than 16 bytes")]
    NameTooLong,
    #[msg("Job is not open")]
    JobNotOpen,
    #[msg("Job budget is exhausted")]
    BudgetExhausted,
    #[msg("Task is not open")]
    TaskNotOpen,
    #[msg("Task is not in the submitted state")]
    TaskNotSubmitted,
    #[msg("Task is neither verified nor past its challenge window")]
    NotSettleable,
    #[msg("Miner is not active")]
    MinerNotActive,
    #[msg("Task is assigned to another miner")]
    NotAssigned,
    #[msg("Job still has unsettled tasks")]
    UnsettledTasks,
    #[msg("Arithmetic overflow")]
    Overflow,
}
