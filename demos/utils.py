from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import re, shutil, fnmatch, yaml, subprocess, json, xml.etree.ElementTree as ET


@dataclass
class DemoMeta:
    slug: str
    title: str
    summary: str
    car_maker_version: str
    tags: List[str]
    state: str
    owner: str
    repo_name: str                 # top-level dir in SVN
    repo_url: str                  # full SVN URL to that dir
    thumbnail_path: Optional[Path] # local cached thumb or None
    exclude: List[str]
    files_meta: List[Dict[str, Any]]

_slugify_re = re.compile(r'[^a-z0-9]+')
def slugify(name: str) -> str:
    return _slugify_re.sub('-', name.lower()).strip('-')

DEFAULT_EXCLUDES = [
    '**/.svn/**', '**/.road_cache/**', '**/.settings/**',
    '**/*.tmp', '**/SimOutput/**', '**/slprj/**',
]

THUMBNAIL_CANDIDATES = ['thumbnail.png','thumbnail.jpg','thumbnail.jpeg','thumbnail.webp']


def load_demos_from_svn_meta(root_url: str, thumb_cache: Path) -> List[DemoMeta]:
    demos: List[DemoMeta] = []
    for name in svn_ls_dirs(root_url):
        slug = slugify(name)
        demo_url = f"{root_url.rstrip('/')}/{name}"
        yml_url = f"{demo_url}/demo.yaml"

        # demo.yaml
        raw_yaml = svn_cat_text(yml_url) or ""
        meta = yaml.safe_load(raw_yaml) if raw_yaml else {}
        if not isinstance(meta, dict): meta = {}

        title = meta.get('title', name)
        summary = meta.get('summary', '')
        cmv = meta.get('car_maker_version', '')
        tags = meta.get('tags', []) or []
        state = (meta.get('state') or 'To be updated').strip()
        owner = (meta.get('owner') or 'not assigned').strip()
        extra_excl = meta.get('exclude') or []
        files_meta = meta.get('files', []) if isinstance(meta.get('files', []), list) else []

        exclude = list(DEFAULT_EXCLUDES)
        if isinstance(extra_excl, list): exclude.extend(extra_excl)

        # thumbnail (fetch once; keep cached file)
        thumb_local: Optional[Path] = None
        for cand in THUMBNAIL_CANDIDATES:
            b = svn_cat_binary(f"{demo_url}/{cand}")
            if b:
                ext = cand.split('.')[-1].lower()
                if ext not in ('png','jpg','jpeg','webp'): ext = 'png'
                path = thumb_cache_path(thumb_cache, slug, ext)
                if not path.exists():  # cache it lazily
                    path.write_bytes(b)
                thumb_local = path
                break

        demos.append(DemoMeta(
            slug=slug,
            title=title,
            summary=summary,
            car_maker_version=cmv,
            tags=tags,
            state=state,
            owner=owner,
            repo_name=name,
            repo_url=demo_url,
            thumbnail_path=thumb_local,
            exclude=exclude,
            files_meta=files_meta
        ))
    return demos


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
    try:
        return subprocess.run(cmd, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n--- STDOUT ---\n{e.stdout}\n--- STDERR ---\n{e.stderr}")

def svn_ls_dirs(root_url: str) -> list[str]:
    out = _run(['svn', 'ls', root_url]).stdout.splitlines()
    return [line.strip('/').strip() for line in out if line.strip().endswith('/')]

def svn_cat_text(url: str) -> Optional[str]:
    try:
        return _run(['svn', 'cat', url]).stdout
    except Exception:
        return None

def svn_cat_binary(url: str) -> Optional[bytes]:
    try:
        cp = subprocess.run(['svn', 'cat', url], check=True, capture_output=True)
        return cp.stdout
    except Exception:
        return None

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

def comments_path(demo_root: Path) -> Path:
    return demo_root / 'comments.json'

def load_comments(demo_root: Path) -> list[dict]:
    f = comments_path(demo_root)
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding='utf-8'))
    except Exception:
        return []

def add_comment(demo_root: Path, user: str, text: str):
    f = comments_path(demo_root)
    comments = load_comments(demo_root)
    comments.append({
        "user": user,
        "text": text.strip(),
        "timestamp": datetime.utcnow().isoformat()
    })
    f.write_text(json.dumps(comments, indent=2), encoding='utf-8')

def _ensure_dir(p: Path): p.mkdir(parents=True, exist_ok=True)

def thumb_cache_path(base: Path, slug: str, ext: str = 'png') -> Path:
    _ensure_dir(base)
    return base / f"{slug}.{ext}"


def svn_list_tree(repo_url: str) -> list[dict]:
    cp = _run(['svn', 'list', '-R', '--xml', repo_url])
    root = ET.fromstring(cp.stdout)
    items = []
    for entry in root.findall('.//entry'):
        kind = entry.get('kind')   # 'file' or 'dir'
        name_el = entry.find('name')
        size_el = entry.find('size')
        if name_el is None: continue
        rel = (name_el.text or '').replace('\\', '/')
        if not rel: continue
        items.append({
            'relpath': rel,
            'size': int(size_el.text) if size_el is not None and size_el.text and size_el.text.isdigit() else 0,
            'is_dir': (kind == 'dir')
        })
    return items

def build_tree_from_list(flat_items: list[dict], exclude_globs: List[str]):
    files = [i for i in flat_items if not i['is_dir']]

    def excluded(rel: str) -> bool:
        return any(fnmatch.fnmatch(rel, pat) for pat in exclude_globs)
    files = [i for i in files if not excluded(i['relpath'])]

    tree = {'name': '', 'type': 'dir', 'children': {}, 'size': 0, '_parent': None}
    for it in files:
        rel = it['relpath'].strip('/')
        parts = rel.split('/') if rel else []
        node = tree
        for idx, part in enumerate(parts):
            is_last = (idx == len(parts)-1)
            if is_last:
                node.setdefault('children', {})
                node['children'].setdefault(part, {
                    'name': part,
                    'type': 'file',
                    'relpath': rel,
                    'size': it['size']
                })
                p = node
                while p is not None:
                    p['size'] = p.get('size', 0) + it['size']
                    p = p.get('_parent')
            else:
                node.setdefault('children', {})
                if part not in node['children']:
                    node['children'][part] = {
                        'name': part, 'type': 'dir',
                        'children': {}, 'size': 0, '_parent': node
                    }
                node = node['children'][part]

    def finalize(n):
        n.pop('_parent', None)
        if isinstance(n.get('children'), dict):
            kids = list(n['children'].values())
            for c in kids: finalize(c)
            kids.sort(key=lambda x: (x['type'] != 'dir', x['name'].lower()))
            n['children'] = kids
    finalize(tree)
    return tree

def load_comments_from_svn(demo_url: str) -> list[dict]:
    """
    Try comments.json first, else comments.txt (lines). Returns a list of {user,text,timestamp?}.
    """
    # JSON form
    js = svn_cat_text(f"{demo_url}/comments.json")
    if js:
        try:
            data = json.loads(js)
            return data if isinstance(data, list) else []
        except Exception:
            pass

    # TXT fallback: each non-empty line is a comment; no user/timestamp
    txt = svn_cat_text(f"{demo_url}/comments.txt")
    if txt:
        out = []
        for line in txt.splitlines():
            t = line.strip()
            if t:
                out.append({"user": "unknown", "text": t})
        return out

    return []
