import argparse
import os
import sys

import renamer_core as core


def dir_path(path):
    if os.path.isdir(path):
        return path
    raise argparse.ArgumentTypeError(f"\"{path}\" is not a valid path!")


def process_command():
    parser = argparse.ArgumentParser(description="Renamer for DLsite works v3.7")
    parser.add_argument('-d', "--DEL", action='store_true', help='delete string in 【】')
    parser.add_argument('-c', "--COVER", action='store_true', help='download cover')
    parser.add_argument('-r', "--RECUR", action='store_true', help='recursively processing')
    parser.add_argument('-n', "--dry-run", dest='DRY_RUN', action='store_true',
                        help='preview the new names without renaming or downloading anything')
    parser.add_argument('-i', "--PATH", type=dir_path, required=True, help='path for processing')
    return parser.parse_args()


def main():
    args = process_command()
    print("===讀取配置文件===\n")
    config = core.load_config(os.path.abspath(os.path.dirname(__file__)), print)
    options = core.Options(delete_brackets=args.DEL, download_cover=args.COVER,
                           recursive=args.RECUR, dry_run=args.DRY_RUN)
    report = core.process(args.PATH, options, config, print)
    return 1 if report.problems else 0


if __name__ == '__main__':
    sys.exit(main())
