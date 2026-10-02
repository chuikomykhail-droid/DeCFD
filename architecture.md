# PROJECT: DeCFD (Decentralized Computational Fluid Dynamics)
## Hackathon MVP Specification

### 1. Context & Goal
We are building an MVP for a Web3 ComputeFi/DePIN project for a hackathon. The product is a decentralized Evolutionary Design Optimizer. It uses a Genetic Algorithm (GA) to find the most aerodynamic shape (lowest drag) by distributing physics simulations across a network of worker nodes. 
**Constraint:** For this hackathon MVP, we are MOCKING the blockchain/network layer. The "network" will run locally on one machine using a Python orchestrator calling C++ binaries via subprocesses.

### 2. Architecture & Tech Stack
- **Worker Node (Physics Engine):** C++ (Lattice Boltzmann Method - LBM D2Q9). Takes shape parameters, runs the 2D wind tunnel simulation, and returns the Drag Coefficient ($C_d$) and a velocity heatmap.
- **Master Node (Orchestrator):** Python. Runs the Genetic Algorithm, manages the "population" of shapes, spawns C++ subprocesses in parallel, and visualizes the results (evolution graphs, heatmaps).

### 3. Folder Structure
Generate and maintain the following monorepo structure:
/DeCFD
  /worker_cpp
    main.cpp (LBM engine)
    Makefile or CMakeLists.txt
  /orchestrator_py
    app.py (GA and subprocess manager)
    visualizer.py (Heatmap/Graph rendering)
    requirements.txt

### 4. Component Requirements

#### A. C++ Worker (LBM D2Q9)
- **Math:** Implement standard 2D LBM with BGK collision operator. Use standard `double` precision. 
- **Grid:** Create a rectangular lattice (e.g., 400x100). Implement inlet velocity on the left, outlet boundary on the right, and periodic/slip boundaries on top/bottom.
- **Input:** Accept geometry parameters via command-line arguments (e.g., `length`, `thickness`, `max_thickness_position` for a teardrop/airfoil shape).
- **Obstacle & Drag:** Rasterize the shape into the grid as solid boundary cells. Implement the "Bounce-back" boundary condition. Calculate the Drag Force (momentum exchange over the obstacle boundary).
- **Output:** Print the final Drag value to `stdout` for Python to parse. Save the final velocity magnitude field to `u_mag.csv`.

#### B. Python Master (Genetic Algorithm)
- **Genotype:** A set of 3-5 numerical parameters defining the shape's geometry (must ensure smooth shapes to avoid simulation crashes).
- **Population:** Initialize a random population of N shapes.
- **Fitness Evaluation:** For each shape, compile (if needed) and run the C++ executable in parallel using `subprocess` or `concurrent.futures`. Read the Drag value from `stdout`. Lower drag = higher fitness.
- **Evolution:** Implement Tournament Selection, Crossover (blend/average), and Mutation (random Gaussian noise to parameters).
- **Visualization:** Use `matplotlib` + `numpy`. Save a combined image every generation: [1] The shape boundary, [2] The velocity heatmap from `u_mag.csv`, [3] A line chart showing the best Drag decreasing over generations.

### 5. Agent Instructions
Please execute the following steps sequentially:
1. Scaffold the directory structure and files.
2. Write the C++ LBM physics engine (`main.cpp`). Ensure it compiles and runs independently.
3. Write the Python Orchestrator (`app.py`) and Visualizer.
4. Run a test generation (Population size = 4, Generations = 3) to verify that Python correctly passes parameters to C++, reads the output, mutates the shapes, and generates the images. Fix any compilation or runtime errors autonomously.