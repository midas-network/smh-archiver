import argparse
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd

# Add parent directory to path to allow imports from utils
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.config import read_repos_yaml


REPORT_CONFIG = os.path.join("hub-config", "report.json")
REPORT_FALLBACK_PATH = "auxiliary-data/reports/"


def select_report_entry(report_config, round_id):
    """Return the ``report.json`` entry whose ``round_id`` matches, or None.

    ``report.json`` is cumulative per tag, so entries for other rounds are ignored.
    """
    for entry in (report_config or {}).get("rounds", []):
        if entry.get("round_id") == round_id:
            return entry
    return None


def resolve_report_file(checkout_dir, declared_path, filename):
    """Locate a declared report file, trying the declared path then the known fallback.

    Returns ``(relative_path, absolute_path)`` or None if the file is nowhere.
    """
    for base in (declared_path, REPORT_FALLBACK_PATH):
        if not base:
            continue
        rel = os.path.join(base, filename)
        full = os.path.join(checkout_dir, rel)
        if os.path.isfile(full):
            return rel, full
    return None


def copy_round_reports(checkout_dir, report_config, round_id, output_dir, ref, run_checkout=None):
    """Copy only the PDFs ``report.json`` lists for ``round_id`` into ``output_dir/reports``.

    ``report_config`` must come from the ``report.json`` at the same ref as ``checkout_dir``:
    ``path`` differs by tag (round 1's is stale at later tags). ``run_checkout`` is called after
    the declared directory is added to the sparse checkout so it materializes. Missing pieces warn
    rather than abort. Returns the sidecar records written.
    """
    entry = select_report_entry(report_config, round_id)
    if entry is None:
        print(f"⚠️ No report.json entry for round {round_id} at {ref}")
        return []
    declared_path = entry.get("path", "")
    for sparse_path in dict.fromkeys([declared_path, REPORT_FALLBACK_PATH]):
        if sparse_path:
            subprocess.run(
                ["git", "-C", checkout_dir, "sparse-checkout", "add", sparse_path],
                check=True,
            )
    if run_checkout is not None:
        run_checkout()
    records = []
    reports_dir = os.path.join(output_dir, "reports")
    for item in entry.get("report", []):
        filename = item.get("filename")
        if not filename:
            continue
        found = resolve_report_file(checkout_dir, declared_path, filename)
        if found is None:
            print(f"⚠️ Report file not found at {ref}: {filename}")
            continue
        rel, full = found
        if os.path.normpath(os.path.dirname(rel)) != os.path.normpath(declared_path):
            print(f"⚠️ {filename} not at declared path {declared_path!r}; used {rel}")
        os.makedirs(reports_dir, exist_ok=True)
        shutil.copy2(full, os.path.join(reports_dir, filename))
        records.append({
            "filename": filename,
            "short_description": item.get("short_description"),
            "path": rel.replace(os.sep, "/"),
            "ref": ref,
        })
    if records:
        Path(reports_dir, "reports.json").write_text(
            json.dumps(records, indent=2) + "\n", encoding="utf-8"
        )
        print(f"📄 Copied {len(records)} report(s) for round {round_id}")
    return records


def clone_and_extract_dirs(repo_url, dirs_to_copy, output_dir, ref='main', ref_type='branch',
                           round_id=None):
    """
    Clone a GitHub repo to a temp folder, copy specific directories, and clean up.

    Parameters:
        repo_url (str): GitHub repo URL (e.g., https://github.com/user/repo.git)
        dirs_to_copy (list of str): List of relative directory paths to copy
        output_dir (str): Where to save the copied directories
        ref (str): Branch name or tag to checkout (default: main)
        ref_type (str): Type of reference ('branch' or 'tag')
    """
    temp_parent = Path(output_dir).resolve().parent
    temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".smh-source-", dir=temp_parent) as tmpdir:
        print(f"📥 Cloning {repo_url} ({ref_type}: {ref}) into temp folder...")
        if ref_type == 'tag':
            # Avoid materializing unrelated auxiliary-data history and large files.
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "--no-checkout",
                    "--depth",
                    "1",
                    repo_url,
                    tmpdir,
                ],
                check=True,
            )
            # Fetch the specific tag
            subprocess.run(
                [
                    "git",
                    "-C",
                    tmpdir,
                    "fetch",
                    "--depth",
                    "1",
                    "origin",
                    f"refs/tags/{ref}:refs/tags/{ref}",
                ],
                check=True,
            )
        else:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "--no-checkout",
                    "--depth",
                    "1",
                    "--branch",
                    ref,
                    repo_url,
                    tmpdir,
                ],
                check=True,
            )

        subprocess.run(
            ["git", "-C", tmpdir, "sparse-checkout", "set", *dirs_to_copy],
            check=True,
        )
        checkout_ref = f"tags/{ref}" if ref_type == "tag" else ref

        def run_checkout():
            subprocess.run(
                [
                    "git",
                    "-C",
                    tmpdir,
                    "-c",
                    "advice.detachedHead=false",
                    "checkout",
                    checkout_ref,
                ],
                check=True,
            )

        run_checkout()

        source_commit = subprocess.run(
            ["git", "-C", tmpdir, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        if round_id:
            report_path = os.path.join(tmpdir, REPORT_CONFIG)
            if os.path.exists(report_path):
                with open(report_path, encoding="utf-8") as fh:
                    report_config = json.load(fh)
                copy_round_reports(
                    tmpdir, report_config, round_id, output_dir, ref, run_checkout
                )
            else:
                print(f"⚠️ {REPORT_CONFIG} not found at {ref}; no reports for round {round_id}")

        for relative_path in dirs_to_copy:
            src = os.path.join(tmpdir, relative_path)
            dst = os.path.join(output_dir, os.path.basename(relative_path))
            if os.path.exists(src):
                print(f"📁 Copying {relative_path} to {dst}")
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                print(f"⚠️ Directory not found: {relative_path}")

        print(f"✅ Done! Selected directories copied to {output_dir}")
        return source_commit


def write_source_snapshot(output_dir, repo_url, ref, ref_type, commit):
    """Record the exact upstream source revision copied into a round."""
    snapshot = {
        "repository": repo_url,
        "ref": ref,
        "ref_type": ref_type,
        "commit": commit,
        "retrieved_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    snapshot_path = Path(output_dir) / "source_snapshot.json"
    if snapshot_path.exists():
        try:
            previous = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        if previous.get("ref") == ref and previous.get("commit") not in (None, commit):
            print(
                f"⚠️ RETAGGED: {ref} previously resolved to {previous['commit']} "
                f"but now resolves to {commit}"
            )
    snapshot_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def get_github_release_tags(repo_url, last_version=True):
    """
    Get a list of release tags from a GitHub repository without cloning.

    Parameters:
        repo_url (str): GitHub repo URL (e.g., https://github.com/user/repo.git)
        last_version (bool): If True, only returns last available version of each tag sharing the
            same date ids, for tags in a `"YYYY-MM-DD-vX"` format (`"-vX"` optional)

    Returns:
        list: A list of release tags
    """
    print(f"🏷️ Fetching tags from {repo_url}...")

    try:
        # Get all tags using ls-remote
        result = subprocess.run(
            ["git", "ls-remote", "--tags", repo_url],
            check=True,
            capture_output=True,
            text=True
        )

        # Parse output to extract tag names
        lines = result.stdout.strip().split('\n')
        tags = []
        for line in lines:
            if not line:
                continue
            # Format is: <commit-hash>\trefs/tags/<tag-name>
            parts = line.split('\t')
            if len(parts) == 2:
                # Extract tag name and remove potential ^{} suffix for annotated tags
                tag = parts[1].replace('refs/tags/', '')
                if not tag.endswith('^{}'):  # Skip the peeled tag references
                    tags.append(tag)

        # Select tag of interest
        if last_version:
            sel_tags = []
            for tag in tags:
                if re.findall("-v", tag):
                    sel_tag = [tag] + re.split("-v", tag)
                else:
                    sel_tag = [tag, tag, '0']
                sel_tags.append(sel_tag)
            tag_df = pd.DataFrame(sel_tags).rename(columns={0: 'tag', 1: 'round', 2: 'version'})
            tags = tag_df.loc[tag_df.groupby(['round'])["version"].idxmax()]["tag"].tolist()

        print(f"Found {len(tags)} tags in repository")
        return tags
    except subprocess.CalledProcessError as e:
        print(f"❌ Error fetching tags: {e}")
        return []

def round_id_from_tag(tag):
    """Return the round id (``YYYY-MM-DD``) for a release tag, dropping any ``-vX`` suffix."""
    return re.split("-v.", tag)[0]


def delete_ignored_files_and_directories(directory, ignore_files_regex):
    """
    Delete files and directories in the specified directory that match the ignore regex.

    Parameters:
        directory (str): Directory to search for files and directories to delete
        ignore_files_regex (list of str): List of regex patterns to match items to delete
    """
    # First pass: Delete matching files
    for root, dirs, files in os.walk(directory):
        for file in files:
            for pattern in ignore_files_regex:
                if re.search(pattern, file):
                    file_path = os.path.join(root, file)
                    print(f"🗑️ Deleting file: {file_path}")
                    os.remove(file_path)
                    break  # No need to check other patterns

    # Second pass: Identify and delete matching directories (bottom-up)
    dirs_to_delete = []
    # NOTE: ".*licen[sc]e.*" applies to everything under data/, including reports/ and
    # model-abstracts/; a license-named file added upstream there would be deleted silently.
    for root, dirs, _ in os.walk(directory, topdown=False):
        for dir_name in dirs:
            for pattern in ignore_files_regex:
                if re.search(pattern, dir_name):
                    dir_path = os.path.join(root, dir_name)
                    dirs_to_delete.append(dir_path)
                    break  # No need to check other patterns

    # Delete directories (from deepest to shallowest)
    for dir_path in sorted(dirs_to_delete, key=len, reverse=True):
        if os.path.exists(dir_path):  # Check if it still exists (might have been deleted as part of a parent)
            print(f"🗑️ Deleting directory: {dir_path}")
            shutil.rmtree(dir_path)


def keep_only_round_files(directory, round_id):
    """
    Delete and/or keep only files related to a specific round.

    Parameters:
        directory (str): Directory to search for files and directories to keep or remove
        round_id (str): Round ID

    """
    # Clean Model output folder
    model_dir = os.path.join(directory, "model-output")
    for root, dirs, files in os.walk(model_dir):
        for file in files:
            if not re.search(round_id, file) and not re.search("README", file):
                file_path = os.path.join(root, file)
                print(f" File remove: {file_path}")
                os.remove(file_path)
    team_model_round = []
    for root, dirs, files in os.walk(model_dir):
        for dir in dirs:
            dir_path = os.path.join(root, dir)
            if len(os.listdir(dir_path)) == 0:
                print(f" Empty folder remove: {dir_path}")
                os.rmdir(dir_path)
            else:
                team_model_round.append(dir)
    # Clean model abstracts: same round, template dropped, only teams with model output
    abstracts_dir = os.path.join(directory, "model-abstracts")
    if os.path.isdir(abstracts_dir):
        shutil.rmtree(os.path.join(abstracts_dir, "template"), ignore_errors=True)
        for team in os.listdir(abstracts_dir):
            team_path = os.path.join(abstracts_dir, team)
            if not os.path.isdir(team_path):
                continue
            if team not in team_model_round:
                print(f" Folder remove: {team_path}")
                shutil.rmtree(team_path)
                continue
            for file in os.listdir(team_path):
                if not file.startswith(f"{round_id}-"):
                    print(f" File remove: {os.path.join(team_path, file)}")
                    os.remove(os.path.join(team_path, file))
            if not os.listdir(team_path):
                os.rmdir(team_path)
    # Clean Metadata folder
    metadata_dir = os.path.join(directory, "model-metadata")
    for root, dirs, files in os.walk(metadata_dir):
        for file in files:
            team_model = re.split(".yaml|.yml", file)[0]
            if team_model not in team_model_round + ["README.md"]:
                file_path = os.path.join(root, file)
                print(f" File remove: {file_path}")
                os.remove(file_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download and update source data from the configured hub repositories."
    )
    parser.add_argument(
        "--rounds",
        nargs="+",
        metavar="ROUND_ID",
        help="Download only these round IDs (e.g., 2023-11-12). Default: all rounds.",
    )
    args = parser.parse_args()
    requested_rounds = set(args.rounds) if args.rounds else None

    # Capture stdout for logging
    output_capture = io.StringIO()

    # Get current date and time for the log
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with redirect_stdout(output_capture):
        print(f"Data update started at: {current_time}\n")

        # Read configuration from YAML
        config = read_repos_yaml()
        repositories = config['repositories']
        base_output_dir = config['data_directory']

        os.makedirs(base_output_dir, exist_ok=True)

        if not repositories:
            print("⚠️ No repositories found in configuration file")
            exit(1)

        print(f"Found {len(repositories)} repositories to process")
        print(f"Base output directory: {base_output_dir}")
        print(f"Ignore files regex: {config['ignore_files_regex']}")
        ignore_files_regex = config['ignore_files_regex']

        # Process each repository
        for repo_config in repositories:
            repo_url = repo_config.get('url')
            directories = list(repo_config["directories"].values())

            if not repo_url:
                print("⚠️ Skipping repository with missing URL")
                continue

            print(f"\n📦 Processing repository: {repo_url}")

            # Get tags for this repository
            tags = get_github_release_tags(repo_url)

            if not tags:
                print(f"⚠️ No tags found for {repo_url}, falling back to branch")
                # Fall back to using the specified branch if no tags found
                branch = repo_config.get('branch', 'main')
                try:
                    output_dir = base_output_dir
                    print(f"Using branch: {branch}")
                    clone_and_extract_dirs(repo_url, directories, output_dir, branch, 'branch')
                except Exception as e:
                    print(f"❌ Error processing repository {repo_url} with branch {branch}: {e}")
                continue

            # Restrict to requested rounds, if any
            if requested_rounds is not None:
                tags = [t for t in tags if round_id_from_tag(t) in requested_rounds]
                matched = sorted({round_id_from_tag(t) for t in tags})
                missing = sorted(requested_rounds - set(matched))
                print(f"Filtering to requested rounds: {', '.join(sorted(requested_rounds))}")
                if missing:
                    print(f"⚠️ No tags found for requested rounds: {', '.join(missing)}")

            # Process each tag
            print(f"Found {len(tags)} tags to process")
            for tag in tags:
                try:
                    # Create tag-specific output directory
                    round_id = round_id_from_tag(tag)
                    tag_output_dir = os.path.join(base_output_dir, round_id)
                    os.makedirs(tag_output_dir, exist_ok=True)

                    print(f"\n🏷️ Processing tag: {tag}")
                    source_commit = clone_and_extract_dirs(
                        repo_url, directories, tag_output_dir, tag, 'tag', round_id
                    )
                    write_source_snapshot(
                        tag_output_dir, repo_url, tag, 'tag', source_commit
                    )
                    print(f"Cleaning Round: {round_id}")
                    round_dir = os.path.join(base_output_dir, round_id)
                    keep_only_round_files(round_dir, round_id)

                except Exception as e:
                    print(f"❌ Error processing tag {tag} for repository {repo_url}: {e}")

        print("\n🎉 All repositories processed!")

        # Apply ignore patterns to the entire output directory structure
        delete_ignored_files_and_directories(base_output_dir, ignore_files_regex)

        print(f"\nData update completed at: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Write the captured output to the log file
    log_content = output_capture.getvalue()
    log_file_path = os.path.join(base_output_dir, "last_update.log")

    # Also print to console
    print(log_content)

    # Write to file
    with open(log_file_path, 'w') as log_file:
        log_file.write(log_content)

    print(f"Log saved to {log_file_path}")
