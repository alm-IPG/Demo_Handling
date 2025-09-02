from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any
import re
import fnmatch
import yaml
import subprocess
import shutil


@dataclass
class DemoMeta:
    slug: str
    title: str
    summary: str
    car_maker_version: str
    tags: List[str]
    root: Path
    thumbnail: Path | None
    exclude: List[str]
    files_meta: List[Dict[str, Any]]
    state: str                
    owner: str  

_slugify_re = re.compile(r'[^a-z0-9]+')

def slugify(name: str) -> str:
    return _slugify_re.sub('-', name.lower()).strip('-')

# Default excludes – keep globs, but we'll also do a path-segment check for ".svn"
DEFAULT_EXCLUDES = [
    '.svn',            # top-level .svn folder guard (segment check below will also catch nested)
    '.svn/**',
    '**/.svn/**',
    '**/.road_cache/**',
    '**/.settings/**',
    '**/*.tmp',
    '**/SimOutput/**',
    '**/slprj/**',
]

THUMBNAIL_CANDIDATES = ['thumbnail.png', 'thumbnail.jpg', 'thumbnail.jpeg', 'thumbnail.webp']

def load_demos(root: Path) -> List[DemoMeta]:
    demos: List[DemoMeta] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        # ✅ Skip control/hidden folders at the top level (e.g., .svn)
        name_l = entry.name.lower()
        if name_l in {'.svn', '__pycache__'} or name_l.startswith('.'):
            continue

        meta: Dict[str, Any] = {}
        yml = entry / 'demo.yaml'
        if yml.exists():
            meta = yaml.safe_load(yml.read_text(encoding='utf-8')) or {}

        title = meta.get('title', entry.name)
        summary = meta.get('summary', '')
        cmv = meta.get('car_maker_version', '')
        tags = meta.get('tags', [])
        state = (meta.get('state') or 'To be updated').strip()
        owner = (meta.get('owner') or 'not assigned').strip()

        # Always apply DEFAULT_EXCLUDES and then add any demo-specific ones
        exclude = list(DEFAULT_EXCLUDES)
        extra_excl = meta.get('exclude') or []
        if isinstance(extra_excl, list):
            exclude.extend(extra_excl)

        # thumbnail
        thumb = None
        media = meta.get('media', {}) if isinstance(meta.get('media', {}), dict) else {}
        custom_thumb = media.get('thumbnail')
        if custom_thumb:
            p = entry / custom_thumb
            if p.exists():
                thumb = p
        if thumb is None:
            for cand in THUMBNAIL_CANDIDATES:
                p = entry / cand
                if p.exists():
                    thumb = p
                    break

        files_meta = meta.get('files', []) if isinstance(meta.get('files', []), list) else []

        demos.append(DemoMeta(
            slug=slugify(entry.name),
            title=title,
            summary=summary,
            car_maker_version=cmv,
            tags=tags,
            root=entry,
            thumbnail=thumb,
            exclude=exclude,
            files_meta=files_meta,
            state=state,                   
            owner=owner,   
        ))
    return demos

def _has_segment(path: Path, segment: str) -> bool:
    # Case-insensitive on Windows; path may be absolute or relative
    seg = segment.lower()
    return any(part.lower() == seg for part in path.parts)

def iter_included_files(root: Path, exclude_globs: List[str]):
    """
    Include everything by default; exclude if:
      - any path segment equals '.svn'  (robust for all nesting levels)
      - OR matches any of the provided glob patterns
    """
    for path in root.rglob('*'):
        if path.is_file():
            # ✅ Strong exclusion: skip anything under a .svn directory
            rel_path = path.relative_to(root)
            if _has_segment(rel_path, '.svn'):
                continue

            rel = rel_path.as_posix()
            if any(fnmatch.fnmatch(rel, pattern) for pattern in exclude_globs):
                continue
            yield path

def list_tree_for_ui(root: Path, exclude_globs: List[str]):
    out = []
    for f in iter_included_files(root, exclude_globs):
        rel = f.relative_to(root).as_posix()
        out.append({'relpath': rel, 'size': f.stat().st_size})
    out.sort(key=lambda x: x['relpath'])
    return out

def build_tree(root: Path, exclude_globs: List[str]):
    """
    Returns a nested tree:
    { 'name': '', 'type': 'dir', 'children': [ ... ], 'size': int }
    Each child is either a dir-node or file-node:
      dir: {'name','type':'dir','children': [...], 'size': int}
      file:{'name','type':'file','relpath': 'Data/Config/x.json','size': int}
    """
    # gather included files first
    files = []
    for f in iter_included_files(root, exclude_globs):
        rel = f.relative_to(root).as_posix()
        files.append((rel, f.stat().st_size))

    # root node
    tree = {'name': '', 'type': 'dir', 'children': {}, 'size': 0}

    for rel, size in files:
        parts = rel.split('/')
        node = tree
        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1
            if is_last:
                node.setdefault('children', {})
                node['children'].setdefault(part, {
                    'name': part,
                    'type': 'file',
                    'relpath': rel,
                    'size': size
                })
                # bubble size up
                _n = node
                while _n is not None:
                    _n['size'] = _n.get('size', 0) + size
                    _n = _n.get('_parent')
            else:
                node.setdefault('children', {})
                if part not in node['children']:
                    node['children'][part] = {
                        'name': part,
                        'type': 'dir',
                        'children': {},
                        'size': 0,
                        '_parent': node,  # temp pointer for size bubbling
                    }
                node = node['children'][part]

    # clean temp pointers and convert dict children -> list (sorted: dirs first, then files)
    def finalize(n):
        n.pop('_parent', None)
        if n.get('children') and isinstance(n['children'], dict):
            items = list(n['children'].values())
            for c in items:
                finalize(c)
            items.sort(key=lambda x: (x['type'] != 'dir', x['name'].lower()))
            n['children'] = items
    finalize(tree)
    return tree


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    # Helper to run svn safely; raises with readable message
    try:
        return subprocess.run(cmd, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        msg = f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{e.stdout}\nSTDERR:\n{e.stderr}"
        raise RuntimeError(msg) from e

def svn_ls_dirs(root_url: str) -> list[str]:
    """
    Returns names of top-level directories under repo root.
    """
    out = _run(['svn', 'ls', root_url]).stdout.splitlines()
    # svn ls ends directories with a trailing slash
    dirs = [line.strip('/') for line in out if line.endswith('/')]
    # skip control/hidden names just in case
    return [d for d in dirs if d and not d.startswith('.') and d.lower() != '.svn']

def svn_export_dir(url: str, dest: Path):
    """
    svn export (clean, no .svn). Overwrite dest if it exists.
    """
    if dest.exists():
        # wipe existing cache for a clean export
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(['svn', 'export', '--force', url, str(dest)])

def sync_all_from_svn(root_url: str, cache_root: Path):
    """
    Export every top-level demo folder from SVN into cache_root/<slug>.
    Assumes each top-level dir is one demo.
    """
    cache_root.mkdir(parents=True, exist_ok=True)
    names = svn_ls_dirs(root_url)
    for name in names:
        slug = slugify(name)
        src_url = f"{root_url.rstrip('/')}/{name}"
        dst = cache_root / name  # keep original name; slug used for routing
        svn_export_dir(src_url, dst)

def load_demos_from_cache(cache_root: Path) -> list[DemoMeta]:
    """
    Reuse existing load_demos() by pointing it at the cache root.
    """
    return load_demos(cache_root)

# --- SVN helpers (additions) ---

def map_slugs_to_repo_names(root_url: str) -> dict[str, str]:
    """
    Returns { slug: repo_dir_name } for all top-level dirs in the repo.
    """
    names = svn_ls_dirs(root_url)  # existing helper
    return {slugify(n): n for n in names}

def sync_one_from_svn(root_url: str, cache_root: Path, slug: str):
    """
    Export exactly one top-level repo dir (matched by slug) into cache.
    """
    slug_map = map_slugs_to_repo_names(root_url)
    repo_name = slug_map.get(slug)
    if not repo_name:
        raise RuntimeError(f"Could not find repo dir for slug '{slug}'. Available: {list(slug_map.values())}")
    src_url = f"{root_url.rstrip('/')}/{repo_name}"
    dst = cache_root / repo_name  # keep original name in cache
    svn_export_dir(src_url, dst)  # existing helper


