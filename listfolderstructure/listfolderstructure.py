from pathlib import Path
import argparse


def format_size(size):
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def walk(path, prefix="", max_depth=None, depth=0, show_hidden=False):
    if max_depth is not None and depth > max_depth:
        return

    try:
        entries = sorted(
            [
                e for e in path.iterdir()
                if show_hidden or not e.name.startswith(".")
            ],
            key=lambda x: (x.is_file(), x.name.lower())
        )
    except PermissionError:
        print(prefix + "`-- [Permission Denied]")
        return

    for i, entry in enumerate(entries):
        last = i == len(entries) - 1
        connector = "`-- " if last else "|-- "

        if entry.is_file():
            size = format_size(entry.stat().st_size)
            print(f"{prefix}{connector}{entry.name} ({size})")
        else:
            print(f"{prefix}{connector}{entry.name}/")
            extension = "    " if last else "|   "
            walk(
                entry,
                prefix + extension,
                max_depth,
                depth + 1,
                show_hidden,
            )


parser = argparse.ArgumentParser()
parser.add_argument("folder", nargs="?", default=".")
parser.add_argument("--depth", type=int)
parser.add_argument("--hidden", action="store_true")

args = parser.parse_args()

root = Path(args.folder).resolve()

print(root)
walk(root, max_depth=args.depth, show_hidden=args.hidden)