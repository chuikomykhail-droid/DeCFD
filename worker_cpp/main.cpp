#include <iostream>
#include <vector>
#include <cmath>
#include <fstream>
#include <string>
#include <iomanip>

const int Nx = 400;
const int Ny = 100;
const int Q = 9;

// D2Q9 velocities and weights
const int cx[Q] = {0, 1, 0, -1, 0, 1, -1, -1, 1};
const int cy[Q] = {0, 0, 1, 0, -1, 1, 1, -1, -1};
const double w[Q] = {4.0/9.0, 1.0/9.0, 1.0/9.0, 1.0/9.0, 1.0/9.0, 1.0/36.0, 1.0/36.0, 1.0/36.0, 1.0/36.0};
const int opp[Q] = {0, 3, 4, 1, 2, 7, 8, 5, 6};

double tau = 0.6;
double u_in = 0.1;
int max_iter = 4000; 

// Using flat arrays for performance
double f[Nx * Ny * Q];
double f_new[Nx * Ny * Q];
bool solid[Nx * Ny];

inline int idx(int x, int y, int i) {
    return (x * Ny + y) * Q + i;
}

inline int sidx(int x, int y) {
    return x * Ny + y;
}

int main(int argc, char** argv) {
    // Default shape parameters
    double L = 60.0;
    std::vector<double> T_pts = {0.0, 20.0, 20.0, 10.0, 0.0}; // 5 control points
    
    // Read from CLI if provided
    if (argc >= 7) {
        L = std::stod(argv[1]);
        T_pts.clear();
        for(int i=2; i<7; i++) {
            T_pts.push_back(std::stod(argv[i]));
        }
    }

    // Initialize solid (rasterizing the shape)
    for (int x = 0; x < Nx; x++) {
        for (int y = 0; y < Ny; y++) {
            double xc = Nx / 4.0; // Place obstacle at 1/4 of the channel
            double yc = Ny / 2.0;
            double x_rel = x - xc; // Front of the shape is at x_rel = 0
            
            bool is_s = false;
            if (x_rel >= 0 && x_rel <= L) {
                int n_segments = T_pts.size() - 1;
                double seg_len = L / n_segments;
                int seg = std::min((int)(x_rel / seg_len), n_segments - 1);
                
                double x0 = seg * seg_len;
                double x1 = (seg + 1) * seg_len;
                double t0 = T_pts[seg];
                double t1 = T_pts[seg + 1];
                
                // Cosine interpolation for smooth curves
                double fraction = (x_rel - x0) / (x1 - x0);
                double mu = (1.0 - std::cos(fraction * 3.14159265358979323846)) / 2.0;
                double half_T = (t0 * (1.0 - mu) + t1 * mu) / 2.0;
                
                if (std::abs(y - yc) <= half_T) is_s = true;
            }
            solid[sidx(x, y)] = is_s;
        }
    }

    // Initialize f (macroscopic equilibrium at inlet velocity)
    for (int x = 0; x < Nx; x++) {
        for (int y = 0; y < Ny; y++) {
            for (int i = 0; i < Q; i++) {
                double cu = 3.0 * (cx[i] * u_in);
                double u2 = u_in * u_in;
                f[idx(x, y, i)] = w[i] * 1.0 * (1.0 + cu + 0.5 * cu * cu - 1.5 * u2);
            }
        }
    }

    double final_drag = 0.0;

    // Main LBM loop
    for (int iter = 0; iter < max_iter; iter++) {
        double drag = 0.0;

        for (int x = 0; x < Nx; x++) {
            for (int y = 0; y < Ny; y++) {
                if (solid[sidx(x, y)]) continue;

                // Macroscopic variables
                double rho = 0, ux = 0, uy = 0;
                for (int i = 0; i < Q; i++) {
                    double val = f[idx(x, y, i)];
                    rho += val;
                    ux += cx[i] * val;
                    uy += cy[i] * val;
                }
                if (rho > 0) {
                    ux /= rho;
                    uy /= rho;
                }

                // Collision and streaming
                for (int i = 0; i < Q; i++) {
                    double cu = 3.0 * (cx[i] * ux + cy[i] * uy);
                    double u2 = ux * ux + uy * uy;
                    double feq = w[i] * rho * (1.0 + cu + 0.5 * cu * cu - 1.5 * u2);
                    
                    // BGK collision
                    double fout = f[idx(x, y, i)] - (f[idx(x, y, i)] - feq) / tau;

                    int nx = x + cx[i];
                    int ny = (y + cy[i] + Ny) % Ny; // Periodic slip on top/bottom

                    if (nx >= 0 && nx < Nx) {
                        if (solid[sidx(nx, ny)]) {
                            // Bounce-back boundary and momentum exchange (drag calculation)
                            f_new[idx(x, y, opp[i])] = fout;
                            drag += 2.0 * fout * cx[i];
                        } else {
                            // Stream to neighbor
                            f_new[idx(nx, ny, i)] = fout;
                        }
                    }
                }
            }
        }

        // Inlet boundary (x = 0): force equilibrium at u_in
        for (int y = 0; y < Ny; y++) {
            if (!solid[sidx(0, y)]) {
                for (int i = 0; i < Q; i++) {
                    double cu = 3.0 * (cx[i] * u_in);
                    double u2 = u_in * u_in;
                    f_new[idx(0, y, i)] = w[i] * 1.0 * (1.0 + cu + 0.5 * cu * cu - 1.5 * u2);
                }
            }
        }

        // Outlet boundary (x = Nx-1): zero gradient extrapolation
        for (int y = 0; y < Ny; y++) {
            if (!solid[sidx(Nx - 1, y)]) {
                for (int i = 0; i < Q; i++) {
                    f_new[idx(Nx - 1, y, i)] = f_new[idx(Nx - 2, y, i)];
                }
            }
        }

        // Update arrays
        for (int i = 0; i < Nx * Ny * Q; i++) {
            f[i] = f_new[i];
        }
        
        final_drag = drag;
    }

    // Output final drag to stdout (so Python can read it)
    std::cout << final_drag << std::endl;

    // Output velocity magnitude field to CSV
    std::ofstream out("u_mag.csv");
    out << std::fixed << std::setprecision(5);
    for (int y = 0; y < Ny; y++) {
        for (int x = 0; x < Nx; x++) {
            if (solid[sidx(x, y)]) {
                out << 0.0;
            } else {
                double rho = 0, ux = 0, uy = 0;
                for (int i = 0; i < Q; i++) {
                    double val = f[idx(x, y, i)];
                    rho += val;
                    ux += cx[i] * val;
                    uy += cy[i] * val;
                }
                if (rho > 0) {
                    ux /= rho;
                    uy /= rho;
                }
                out << std::sqrt(ux * ux + uy * uy);
            }
            if (x < Nx - 1) out << ",";
        }
        out << "\n";
    }
    out.close();

    return 0;
}

