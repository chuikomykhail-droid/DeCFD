import numpy as np
import matplotlib.pyplot as plt
import os

csv_file = "u_mag.csv"

if not os.path.exists(csv_file):
    print(f"Error: {csv_file} not found. Run the C++ engine first!")
    exit(1)

# Read the CSV file
print(f"Reading {csv_file}...")
data = np.loadtxt(csv_file, delimiter=',')

# Plot the heatmap
plt.figure(figsize=(12, 3))
plt.title("Velocity Magnitude (u_mag)")
plt.imshow(data, cmap='jet', origin='upper')
plt.colorbar(label="Velocity")
plt.xlabel("X (Grid cells)")
plt.ylabel("Y (Grid cells)")

# Save and show
plt.savefig("test_render.png", dpi=300, bbox_inches='tight')
print("Saved visualization to test_render.png")
plt.show()

