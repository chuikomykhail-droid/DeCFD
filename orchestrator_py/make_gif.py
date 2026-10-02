import os
import glob
import re
try:
    from PIL import Image
except ImportError:
    print("Pillow library is required. Please install it using: pip install Pillow")
    exit(1)

def create_gif():
    results_dir = os.path.join(os.path.dirname(__file__), "results")
    
    # Find all generation PNGs
    files = glob.glob(os.path.join(results_dir, "generation_*.png"))
    if not files:
        print("No generation images found in results/ folder.")
        return
        
    # Extract generation number to sort them properly
    def get_gen_num(filename):
        basename = os.path.basename(filename)
        # Matches "generation_5.png" or "generation_5_1639920.png"
        match = re.search(r'generation_(\d+)', basename)
        if match:
            return int(match.group(1))
        return -1

    # Filter and sort files by generation number
    files = [f for f in files if get_gen_num(f) != -1]
    files.sort(key=get_gen_num)
    
    # Handle duplicates (if visualizer used the fallback saving mechanism with timestamps)
    # We keep the latest one for each generation
    unique_files = {}
    for f in files:
        gen = get_gen_num(f)
        unique_files[gen] = f
            
    sorted_files = [unique_files[g] for g in sorted(unique_files.keys())]
    
    print(f"Found {len(sorted_files)} frames. Stitching into GIF...")
    
    # Load images
    images = []
    for f in sorted_files:
        try:
            images.append(Image.open(f))
        except Exception as e:
            print(f"Error loading {f}: {e}")
            
    if not images:
        print("No valid images to process.")
        return
        
    # Save as animated GIF
    gif_path = os.path.join(results_dir, "evolution_animation.gif")
    
    # Save the first image, and append the rest
    images[0].save(
        gif_path,
        save_all=True,
        append_images=images[1:],
        duration=600,  # 600 milliseconds per frame
        loop=0         # 0 means loop infinitely
    )
    
    print(f"Success! 🚀 GIF saved to: {gif_path}")

if __name__ == "__main__":
    create_gif()
