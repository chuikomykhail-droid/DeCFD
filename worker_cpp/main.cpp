#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <cmath>
#include <climits>
#include <vector>
#include <string>
#include <algorithm>
#include <chrono>

#ifdef _MSC_VER
#include <malloc.h>
#define ALIGNED_ALLOC(align, size) _aligned_malloc(size, align)
#else
#define ALIGNED_ALLOC(align, size) std::aligned_alloc(align, ((size + align - 1) / align) * align)
#endif

#ifndef NX
#define NX 480
#endif
#ifndef NY
#define NY 160
#endif
#ifndef REAL
#define REAL float
#endif
#ifndef XCEN
#define XCEN (0.30 * NX)          // body centre (chord midpoint), x
#endif
#ifndef YCEN
#define YCEN (0.5 * (NY - 1))     // body centre, y (rows 0..NY-1)
#endif

typedef REAL real;
constexpr int    Nx = NX, Ny = NY, Rows = NY + 2;   // rows 0 and Ny+1 are ghost rows
constexpr size_t PLANE = (size_t)Nx * Rows;
constexpr int    Q = 9;
constexpr int    CX[Q]    = {0, 1, 0, -1, 0, 1, -1, -1, 1};
constexpr int    CY[Q]    = {0, 0, 1, 0, -1, 1, 1, -1, -1};
constexpr double WD[Q]    = {4./9, 1./9, 1./9, 1./9, 1./9, 1./36, 1./36, 1./36, 1./36};
constexpr int    FLIPY[Q] = {0, 1, 4, 3, 2, 8, 7, 6, 5};

constexpr real w0 = real(4.0 / 9.0), w1 = real(1.0 / 9.0), w5 = real(1.0 / 36.0);
constexpr real HALF = real(0.5);

static real* alloc_real(size_t n) {
    size_t bytes = ((n * sizeof(real) + 63) / 64) * 64;
    void* p = ALIGNED_ALLOC(64, bytes);
    if (!p) { std::fprintf(stderr, "out of memory\n"); std::exit(1); }
    std::memset(p, 0, bytes);
    return (real*)p;
}

template <bool MASKED>
static inline void row_kernel(const real* const* s, real* const* d, const real* sol,
                              int y, int xb, int xe, real omega) {
    if (xb >= xe) return;
    const size_t r0 = (size_t)y * Nx, rm = r0 - Nx, rp = r0 + Nx;
    const real* __restrict s0 = s[0]; const real* __restrict s1 = s[1];
    const real* __restrict s2 = s[2]; const real* __restrict s3 = s[3];
    const real* __restrict s4 = s[4]; const real* __restrict s5 = s[5];
    const real* __restrict s6 = s[6]; const real* __restrict s7 = s[7];
    const real* __restrict s8 = s[8];
    real* __restrict d0 = d[0]; real* __restrict d1 = d[1]; real* __restrict d2 = d[2];
    real* __restrict d3 = d[3]; real* __restrict d4 = d[4]; real* __restrict d5 = d[5];
    real* __restrict d6 = d[6]; real* __restrict d7 = d[7]; real* __restrict d8 = d[8];
    const real* __restrict sl = sol;

#if defined(__GNUC__)
#pragma GCC ivdep
#endif
    for (int x = xb; x < xe; ++x) {
        const size_t p = r0 + x;
        real f0 = s0[p];
        real f1 = s1[p - 1];
        real f2 = s2[rm + x];
        real f3 = s3[p + 1];
        real f4 = s4[rp + x];
        real f5 = s5[rm + x - 1];
        real f6 = s6[rm + x + 1];
        real f7 = s7[rp + x + 1];
        real f8 = s8[rp + x - 1];

        if (MASKED) {
            const real b1 = s3[p], b2 = s4[p], b3 = s1[p], b4 = s2[p];
            const real b5 = s7[p], b6 = s8[p], b7 = s5[p], b8 = s6[p];
            f1 = sl[p - 1]      > HALF ? b1 : f1;
            f2 = sl[rm + x]     > HALF ? b2 : f2;
            f3 = sl[p + 1]      > HALF ? b3 : f3;
            f4 = sl[rp + x]     > HALF ? b4 : f4;
            f5 = sl[rm + x - 1] > HALF ? b5 : f5;
            f6 = sl[rm + x + 1] > HALF ? b6 : f6;
            f7 = sl[rp + x + 1] > HALF ? b7 : f7;
            f8 = sl[rp + x - 1] > HALF ? b8 : f8;
        }

        const real rho  = f0 + f1 + f2 + f3 + f4 + f5 + f6 + f7 + f8;
        const real inv  = real(1) / rho;
        const real ux   = ((f1 - f3) + (f5 - f6) + (f8 - f7)) * inv;
        const real uy   = ((f2 - f4) + (f5 - f7) + (f6 - f8)) * inv;

        const real base = real(1) - real(1.5) * (ux * ux + uy * uy);
        const real wr1 = w1 * rho, wr5 = w5 * rho;

        real o0 = f0 + omega * (w0 * rho * base - f0);
        real o1 = f1 + omega * (wr1 * (base + real(3) * ux  + real(4.5) * ux * ux) - f1);
        real o3 = f3 + omega * (wr1 * (base - real(3) * ux  + real(4.5) * ux * ux) - f3);
        real o2 = f2 + omega * (wr1 * (base + real(3) * uy  + real(4.5) * uy * uy) - f2);
        real o4 = f4 + omega * (wr1 * (base - real(3) * uy  + real(4.5) * uy * uy) - f4);

        const real c5 = ux + uy, c6 = uy - ux;
        real o5 = f5 + omega * (wr5 * (base + real(3) * c5 + real(4.5) * c5 * c5) - f5);
        real o7 = f7 + omega * (wr5 * (base - real(3) * c5 + real(4.5) * c5 * c5) - f7);
        real o6 = f6 + omega * (wr5 * (base + real(3) * c6 + real(4.5) * c6 * c6) - f6);
        real o8 = f8 + omega * (wr5 * (base - real(3) * c6 + real(4.5) * c6 * c6) - f8);

        if (MASKED) {
            const bool sf = sl[p] > HALF;
            o0 = sf ? w0 : o0;
            o1 = sf ? w1 : o1; o2 = sf ? w1 : o2; o3 = sf ? w1 : o3; o4 = sf ? w1 : o4;
            o5 = sf ? w5 : o5; o6 = sf ? w5 : o6; o7 = sf ? w5 : o7; o8 = sf ? w5 : o8;
        }

        d0[p] = o0; d1[p] = o1; d2[p] = o2; d3[p] = o3; d4[p] = o4;
        d5[p] = o5; d6[p] = o6; d7[p] = o7; d8[p] = o8;
    }
}

static void step(real* const* s, real* const* d, const real* sol,
                 const int* xlo, const int* xhi, real omega) {
#pragma omp parallel for schedule(static)
    for (int y = 1; y <= Ny; ++y) {
        const int a = xlo[y], b = xhi[y];
        if (a > b) {
            row_kernel<false>(s, d, sol, y, 1, Nx - 1, omega);
        } else {
            row_kernel<false>(s, d, sol, y, 1, a, omega);
            row_kernel<true >(s, d, sol, y, a, b + 1, omega);
            row_kernel<false>(s, d, sol, y, b + 1, Nx - 1, omega);
        }
    }
}

static void apply_bc(real* const* d, const real* feq_in) {
    for (int r = 1; r <= Ny; ++r) {
        const size_t p0 = (size_t)r * Nx;
        for (int i = 0; i < Q; ++i) {
            d[i][p0] = feq_in[i];                          // inlet: equilibrium
            d[i][p0 + Nx - 1] = d[i][p0 + Nx - 2];         // outlet: zero gradient
        }
    }
    for (int i = 0; i < Q; ++i) {
#ifdef PERIODIC_Y
        std::memcpy(d[i], d[i] + (size_t)Ny * Nx, Nx * sizeof(real));
        std::memcpy(d[i] + (size_t)(Ny + 1) * Nx, d[i] + (size_t)Nx, Nx * sizeof(real));
#else
        std::memcpy(d[i], d[FLIPY[i]] + (size_t)Nx, Nx * sizeof(real));
        std::memcpy(d[i] + (size_t)(Ny + 1) * Nx, d[FLIPY[i]] + (size_t)Ny * Nx, Nx * sizeof(real));
#endif
    }
}

struct Shape {
    double L, T[5], alpha_deg, camber;
    bool cylinder;   // validation mode: circle of diameter L instead of the airfoil
    double yshift;   // body centre offset from the channel centreline, cells
};

static bool build_geometry(const Shape& sh, std::vector<real>& sol,
                           std::vector<int>& smin, std::vector<int>& smax) {
    const double PI = 3.14159265358979323846;
    const double a = sh.alpha_deg * PI / 180.0;
    const double ca = std::cos(a), sa = std::sin(a);

    smin.assign(Rows, INT_MAX); smax.assign(Rows, -1);
    sol.assign(PLANE, real(0));
    int count = 0;

    for (int r = 0; r < Ny; ++r) {
        const int row = r + 1;
        for (int x = 0; x < Nx; ++x) {
            const double dx = x - (double)(XCEN), dy = r - (double)(YCEN) - sh.yshift;
            const double xb = ca * dx - sa * dy;       // world -> body frame
            const double yb = sa * dx + ca * dy;
            const double s = xb + sh.L / 2.0;

            bool is_s = false;
            if (sh.cylinder) {
                is_s = dx * dx + dy * dy <= 0.25 * sh.L * sh.L;
            } else if (s >= 0.0 && s <= sh.L) {
                const int nseg = 4;
                const double seg_len = sh.L / nseg;
                const int seg = std::min((int)(s / seg_len), nseg - 1);
                const double x0 = seg * seg_len, x1 = (seg + 1) * seg_len;
                const double fr = (s - x0) / (x1 - x0);
                const double mu = (1.0 - std::cos(fr * PI)) / 2.0;
                const double halfT = (sh.T[seg] * (1.0 - mu) + sh.T[seg + 1] * mu) / 2.0;
                const double yc = 4.0 * sh.camber * (s / sh.L) * (1.0 - s / sh.L);
                is_s = std::fabs(yb - yc) <= halfT;
            }

            if (is_s) {
                if (x < 3 || x > Nx - 4 || r < 2 || r > Ny - 3) return false;   // too close to boundary
                sol[(size_t)row * Nx + x] = real(1);
                smin[row] = std::min(smin[row], x);
                smax[row] = std::max(smax[row], x);
                ++count;
            }
        }
    }
    return count > 0;
}

static void velocity(real* const* f, const std::vector<real>& sol, std::vector<float>& ux, std::vector<float>& uy) {
    ux.assign(PLANE, 0.f); uy.assign(PLANE, 0.f);
    for (int r = 1; r <= Ny; ++r)
        for (int x = 0; x < Nx; ++x) {
            const size_t p = (size_t)r * Nx + x;
            if (sol[p] > HALF) continue;
            double rho = 0, jx = 0, jy = 0;
            for (int i = 0; i < Q; ++i) { const double v = f[i][p]; rho += v; jx += CX[i] * v; jy += CY[i] * v; }
            ux[p] = (float)(jx / rho); uy[p] = (float)(jy / rho);
        }
}

static inline float vorticity_at(const std::vector<float>& ux, const std::vector<float>& uy,
                                 const std::vector<real>& sol, int r, int x) {
    const size_t p = (size_t)r * Nx + x;
    if (x > 0 && x < Nx - 1 && r > 1 && r < Ny && sol[p] <= HALF)
        return 0.5f * (uy[p + 1] - uy[p - 1]) - 0.5f * (ux[p + Nx] - ux[p - Nx]);
    return 0.f;
}

static void write_csv(const char* path, const std::vector<float>& v, const char* fmt) {
    FILE* o = std::fopen(path, "w");
    if (!o) return;
    for (int r = Ny; r >= 1; --r)
        for (int x = 0; x < Nx; ++x) {
            std::fprintf(o, fmt, v[(size_t)r * Nx + x]);
            std::fputc(x + 1 < Nx ? ',' : '\n', o);
        }
    std::fclose(o);
}

// Vorticity snapshot as raw float32, Ny rows top to bottom x Nx columns (same layout as the CSVs)
static void write_vort_bin(const char* path, real* const* f, const std::vector<real>& sol) {
    std::vector<float> ux, uy, w((size_t)Nx * Ny);
    velocity(f, sol, ux, uy);
    for (int r = Ny; r >= 1; --r)
        for (int x = 0; x < Nx; ++x) w[(size_t)(Ny - r) * Nx + x] = vorticity_at(ux, uy, sol, r, x);
    FILE* o = std::fopen(path, "wb");
    if (!o) return;
    std::fwrite(w.data(), sizeof(float), w.size(), o);
    std::fclose(o);
}

static void write_fields(const char* csv, const char* vort, const char* ux_csv, const char* uy_csv,
                         real* const* f, const std::vector<real>& sol) {
    std::vector<float> ux, uy;
    velocity(f, sol, ux, uy);
    if (ux_csv) write_csv(ux_csv, ux, "%.6f");
    if (uy_csv) write_csv(uy_csv, uy, "%.6f");

    if (csv) {
        FILE* o = std::fopen(csv, "w");
        if (o) {
            for (int r = Ny; r >= 1; --r) {
                for (int x = 0; x < Nx; ++x) {
                    const size_t p = (size_t)r * Nx + x;
                    std::fprintf(o, "%.4f%c", std::sqrt(ux[p] * ux[p] + uy[p] * uy[p]), x + 1 < Nx ? ',' : '\n');
                }
            }
            std::fclose(o);
        }
    }
    if (vort) {
        FILE* o = std::fopen(vort, "w");
        if (o) {
            for (int r = Ny; r >= 1; --r) {
                for (int x = 0; x < Nx; ++x)
                    std::fprintf(o, "%.5f%c", vorticity_at(ux, uy, sol, r, x), x + 1 < Nx ? ',' : '\n');
            }
            std::fclose(o);
        }
    }
}

struct Link { uint32_t p; uint32_t j; };

int main(int argc, char** argv) {
    std::vector<double> pos;
    int steps = 6000, avg = 2000, snap_every = 0; double tau = 0.6, uin = 0.1, yshift = 0.0;
    const char *csv = nullptr, *vort = nullptr, *ux_csv = nullptr, *uy_csv = nullptr;
    const char *history = nullptr, *snap_prefix = nullptr;
    bool verbose = false, cylinder = false;

    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        auto next = [&]() -> const char* { return (i + 1 < argc) ? argv[++i] : (std::fprintf(stderr, "missing value for %s\n", a.c_str()), std::exit(2), nullptr); };
        if      (a == "--steps")   steps = std::atoi(next());
        else if (a == "--avg")     avg = std::atoi(next());
        else if (a == "--tau")     tau = std::atof(next());
        else if (a == "--uin")     uin = std::atof(next());
        else if (a == "--csv")     csv = next();
        else if (a == "--vort")    vort = next();
        else if (a == "--ux")      ux_csv = next();
        else if (a == "--uy")      uy_csv = next();
        else if (a == "--history") history = next();
        else if (a == "--snap-every")  snap_every = std::atoi(next());
        else if (a == "--snap-prefix") snap_prefix = next();
        else if (a == "--yshift")  yshift = std::atof(next());
        else if (a == "--cylinder") cylinder = true;
        else if (a == "--verbose") verbose = true;
        else if (a.rfind("--", 0) == 0) { std::fprintf(stderr, "unknown flag %s\n", a.c_str()); return 2; }
        else pos.push_back(std::atof(a.c_str()));
    }

    if (pos.size() < (cylinder ? 1u : 6u)) {
        std::fprintf(stderr,
            "usage: %s L t0 t1 t2 t3 t4 [alpha_deg] [camber] [options]\n"
            "       %s --cylinder D [options]\n"
            "options: --steps N --avg N --tau T --uin U --yshift Y\n"
            "         --csv f (|u|) --vort f --ux f --uy f --history f (fx,fy per averaged step)\n"
            "         --snap-every N --snap-prefix P (float32 vorticity snapshots during averaging)\n",
            argv[0], argv[0]);
        return 2;
    }

    Shape sh = {};
    sh.L = pos[0];
    sh.cylinder = cylinder;
    sh.yshift = yshift;
    if (!cylinder) {
        for (int i = 0; i < 5; ++i) sh.T[i] = pos[1 + i];
        sh.alpha_deg = pos.size() > 6 ? pos[6] : 0.0;
        sh.camber    = pos.size() > 7 ? pos[7] : 0.0;
    }

    if (!(sh.L >= 4.0) || sh.L > 0.6 * Nx) { std::fprintf(stderr, "bad chord\n"); return 2; }
    for (int i = 0; i < 5; ++i) if (!(sh.T[i] >= 0.0)) { std::fprintf(stderr, "bad thickness\n"); return 2; }
    if (snap_every < 0 || (snap_every > 0 && !snap_prefix)) { std::fprintf(stderr, "--snap-every needs --snap-prefix\n"); return 2; }
    if (steps < 1 || avg < 1 || avg > steps || !(tau > 0.5) || !(uin > 0.0 && uin < 0.2)) { std::fprintf(stderr, "bad numeric options\n"); return 2; }

    std::vector<real> sol; std::vector<int> smin, smax;
    if (!build_geometry(sh, sol, smin, smax)) { std::fprintf(stderr, "empty body or body too close to the domain boundary\n"); return 3; }

    std::vector<int> xlo(Rows, 1), xhi(Rows, 0);
    for (int y = 1; y <= Ny; ++y) {
        int lo = std::min(smin[y - 1], std::min(smin[y], smin[y + 1]));
        int hi = std::max(smax[y - 1], std::max(smax[y], smax[y + 1]));
        if (hi < 0) { xlo[y] = 1; xhi[y] = 0; }
        else { xlo[y] = std::max(1, lo - 1); xhi[y] = std::min(Nx - 2, hi + 1); }
    }

    std::vector<Link> links;
    for (int r = 1; r <= Ny; ++r)
        for (int x = 1; x <= Nx - 2; ++x) {
            const size_t p = (size_t)r * Nx + x;
            if (sol[p] > HALF) continue;
            for (int j = 1; j < Q; ++j)
                if (sol[(size_t)(r + CY[j]) * Nx + (x + CX[j])] > HALF) links.push_back({(uint32_t)p, (uint32_t)j});
        }

    real feq_in[Q];
    for (int i = 0; i < Q; ++i) {
        const double cu = CX[i] * uin;
        feq_in[i] = (real)(WD[i] * (1.0 + 3.0 * cu + 4.5 * cu * cu - 1.5 * uin * uin));
    }

    real* bufA[Q]; real* bufB[Q];
    for (int i = 0; i < Q; ++i) {
        bufA[i] = alloc_real(PLANE); bufB[i] = alloc_real(PLANE);
        for (size_t p = 0; p < PLANE; ++p) {
            const real v = (sol[p] > HALF) ? (real)WD[i] : feq_in[i];
            bufA[i][p] = v; bufB[i][p] = v;
        }
    }

    real *const *src = bufA, *const *dst = bufB;
    const real omega = (real)(1.0 / tau);
    std::vector<double> hx, hy; hx.reserve(avg); hy.reserve(avg);

    const auto t0 = std::chrono::steady_clock::now();
    for (int it = 0; it < steps; ++it) {
        step(src, dst, sol.data(), xlo.data(), xhi.data(), omega);
        apply_bc(const_cast<real* const*>(dst), feq_in);
        if (it >= steps - avg) {
            double fx = 0.0, fy = 0.0;
            for (const Link& l : links) {
                const double g = dst[l.j][l.p];
                fx += 2.0 * g * CX[l.j];
                fy += 2.0 * g * CY[l.j];
            }
            hx.push_back(fx); hy.push_back(fy);
            const int k = it - (steps - avg);
            if (snap_every > 0 && k % snap_every == 0) {
                char path[1024];
                std::snprintf(path, sizeof path, "%s_%05d.bin", snap_prefix, k / snap_every);
                write_vort_bin(path, dst, sol);
            }
        }
        std::swap(src, dst);
    }

    const double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();

    auto stats = [&](const std::vector<double>& h, double& mean, double& spread) {
        double s = 0; for (double v : h) s += v; mean = s / h.size();
        const int nb = 4; const size_t bl = h.size() / nb;
        double mn = 1e300, mx = -1e300;
        if (bl == 0) { spread = 0; return; }
        for (int b = 0; b < nb; ++b) {
            double bs = 0; for (size_t k = 0; k < bl; ++k) bs += h[b * bl + k];
            bs /= bl; mn = std::min(mn, bs); mx = std::max(mx, bs);
        }
        spread = (mx - mn);
    };

    double fx, fy, sx, sy; stats(hx, fx, sx); stats(hy, fy, sy);
    if (!std::isfinite(fx) || !std::isfinite(fy)) { std::fprintf(stderr, "diverged (NaN)\n"); return 4; }

    const double q = 0.5 * uin * uin * sh.L;
    std::printf("{\"fx\":%.9g,\"fy\":%.9g,\"cd\":%.9g,\"cl\":%.9g,\"fx_spread\":%.3g,\"fy_spread\":%.3g,\"steps\":%d,\"avg\":%d,\"nx\":%d,\"ny\":%d}\n",
                fx, fy, fx / q, fy / q, sx, sy, steps, avg, Nx, Ny);

    if (history) {
        FILE* o = std::fopen(history, "w");
        if (o) {
            std::fprintf(o, "step,fx,fy\n");
            for (size_t k = 0; k < hx.size(); ++k)
                std::fprintf(o, "%d,%.9g,%.9g\n", steps - avg + (int)k + 1, hx[k], hy[k]);
            std::fclose(o);
        }
    }
    if (csv || vort || ux_csv || uy_csv) write_fields(csv, vort, ux_csv, uy_csv, const_cast<real* const*>(src), sol);
    if (verbose) {
        const double mlups = (double)Nx * Ny * steps / secs / 1e6;
        std::fprintf(stderr, "grid %dx%d, %d steps, %.2f s, %.1f MLUPS, %zu links, real=%zu bytes\n",
                     Nx, Ny, steps, secs, mlups, links.size(), sizeof(real));
    }
    return 0;
}

