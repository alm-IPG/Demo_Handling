from pathlib import Path
from django.conf import settings
from django.http import Http404, StreamingHttpResponse, HttpRequest, HttpResponse, FileResponse
from django.shortcuts import render, redirect
from .utils import load_demos, list_tree_for_ui, iter_included_files
from django.views.decorators.http import require_POST
from django.contrib import messages
from .utils import (
    load_demos_from_svn_meta, svn_list_tree, build_tree_from_list,
    load_comments_from_svn
)
import io
import zipfile
import mimetypes 


def _get_demos():
    # Always fetch fresh metadata on each request
    if getattr(settings, 'USE_SVN', False) and getattr(settings, 'USE_SVN_METADATA_ONLY', False):
        root_url = getattr(settings, 'SVN_ROOT_URL')
        thumb_cache = Path(getattr(settings, 'THUMB_CACHE'))
        return load_demos_from_svn_meta(root_url, thumb_cache)
    raise RuntimeError("Configure USE_SVN=True and USE_SVN_METADATA_ONLY=True for metadata-only mode.")


def gallery(request: HttpRequest) -> HttpResponse:
    demos = _get_demos()
    q = request.GET.get('q', '').lower().strip()
    tag = request.GET.get('tag', '').strip()
    cm = request.GET.get('cm', '').strip()

    def cm_bucket(v: str) -> str:
        try:
            return f"CM{str(v).split('.')[0]}" if v else ''
        except Exception:
            return ''

    if q:
        demos = [d for d in demos if q in d.title.lower() or q in d.summary.lower()]
    if tag:
        demos = [d for d in demos if tag in d.tags]
    if cm:
        demos = [d for d in demos if cm_bucket(d.car_maker_version) == cm]

    # attach cm_bucket for template use
    for d in demos:
        d.cm_bucket = cm_bucket(d.car_maker_version)

    all_tags = sorted({t for d in demos for t in d.tags})
    all_cm = sorted({cm_bucket(d.car_maker_version) for d in demos if d.car_maker_version})

    return render(request, 'demos/gallery.html', {
        'demos': demos,
        'q': q,
        'tag': tag,
        'cm': cm,
        'all_tags': all_tags,
        'all_cm': all_cm,
    })

def detail(request: HttpRequest, slug: str) -> HttpResponse:
    demos = _get_demos()
    d = next((x for x in demos if x.slug == slug), None)
    if not d:
        raise Http404('Demo not found')

    flat = svn_list_tree(d.repo_url)                 # names + sizes from SVN
    file_tree = build_tree_from_list(flat, d.exclude)
    cm_bucket = f"CM{str(d.car_maker_version).split('.')[0]}" if d.car_maker_version else ''
    comments = load_comments_from_svn(d.repo_url)    # READ-ONLY

    return render(request, 'demos/detail.html', {
        'demo': d, 'cm_bucket': cm_bucket, 'tree': file_tree, 'comments': comments
    })


def thumb(request, slug: str):
    demos = _get_demos()
    d = next((x for x in demos if x.slug == slug), None)
    if not d or not d.thumbnail_path or not d.thumbnail_path.exists():
        raise Http404('Thumbnail not found')
    return FileResponse(open(d.thumbnail_path, 'rb'))

