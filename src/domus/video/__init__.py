"""Site videos into 3D point clouds on the site's grid: `domus video <step>`. See docs/video.md."""

import importlib
import sys

STEPS = {
    "auto": ("auto", "From a scan to the model, with no hand work"),
    "frames": ("extract_frames", "Pull the sharpest frames out of a video"),
    "reconstruct": ("colmap_sfm", "Reconstruct a clip with COLMAP (the one to start with)"),
    "reconstruct-chain": ("video_sfm", "Reconstruct a clip with the built-in chain of views"),
    "merge": ("merge_sfm", "Join two reconstructions of a clip that share frames"),
    "split": ("split_sfm", "Cut a time span out of a reconstruction"),
    "link": ("link_sfm", "Find what carries one reconstruction into another's frame"),
    "link-picks": ("link_picks", "Link two reconstructions through points marked in both"),
    "place": ("georef_sfm", "Place a reconstruction on the site's grid"),
    "pick": ("triangulate_picks", "Put points marked in frames on the grid"),
    "dense": ("dense_sfm", "Dense point cloud from a placed reconstruction"),
    "export": ("export_cloud", "Combine the clouds in [video] clouds for the build"),
    "plot": ("plot_video_cloud", "Plan view of a cloud over the aerial, for checking"),
}


def main(argv):
    if not argv or argv[0] not in STEPS:
        print("usage: domus video <step> [args]\n\nSteps, each with its own --help:")
        for name, (_, what) in STEPS.items():
            print(f"  {name:18} {what}")
        return 0 if not argv else 2
    module = importlib.import_module(f".{STEPS[argv[0]][0]}", __name__)
    sys.argv = [f"domus video {argv[0]}", *argv[1:]]
    return module.main()
