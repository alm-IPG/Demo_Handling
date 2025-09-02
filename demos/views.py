from pathlib import Path
from django.conf import settings
from django.http import Http404, StreamingHttpResponse, HttpRequest, HttpResponse, FileResponse
from django.shortcuts import render, redirect
from .utils import load_demos, list_tree_for_ui, iter_included_files
from django.views.decorators.http import require_POST
from django.contrib import messages
import io
import zipfile
import mimetypes 


def _get_demos():
    use_svn = getattr(settings, 'USE_SVN', False)
    if use_svn:
        cache_root = Path(getattr(settings, 'CACHE_ROOT'))
        if not cache_root.exists() or not any(cache_root.iterdir()):
            # Empty cache? Do a first-time sync.
            from .utils import sync_all_from_svn, load_demos_from_cache
            root_url = getattr(settings, 'SVN_ROOT_URL')
            sync_all_from_svn(root_url, cache_root)
        from .utils import load_demos_from_cache
        return load_demos_from_cache(cache_root)
    else:
        root = Path(getattr(settings, 'DEMOS_ROOT', r'C:\SVN-Demos'))
        if not root.exists():
            raise RuntimeError(f'DEMOS_ROOT not found: {root}')
        return load_demos(root)

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
    from .utils import load_demos, list_tree_for_ui, iter_included_files, build_tree
    demos = _get_demos()
    d = next((x for x in demos if x.slug == slug), None)
    if not d:
        raise Http404('Demo not found')
    file_tree = build_tree(d.root, d.exclude)
    cm_bucket = f"CM{str(d.car_maker_version).split('.')[0]}" if d.car_maker_version else ''
    return render(request, 'demos/detail.html', {
        'demo': d,
        'cm_bucket': cm_bucket,
        'tree': file_tree,
    })

def download_all(request: HttpRequest, slug: str) -> HttpResponse:
    demos = _get_demos()
    d = next((x for x in demos if x.slug == slug), None)
    if not d:
        raise Http404('Demo not found')

    total = 0
    files = []
    max_bytes = getattr(settings, 'MAX_ZIP_BYTES', 5 * 1024 * 1024 * 1024)

    for f in iter_included_files(d.root, d.exclude):
        size = f.stat().st_size
        total += size
        files.append((f, size))
        if total > max_bytes:
            return HttpResponse('Selection exceeds 5 GB cap. Narrow contents or adjust settings.', status=413)

    def zip_generator():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode='w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for f, _ in files:
                arcname = f.relative_to(d.root).as_posix()
                zf.write(f, arcname)
        buf.seek(0)
        chunk = buf.read(1024 * 1024)
        while chunk:
            yield chunk
            chunk = buf.read(1024 * 1024)

    filename = f"{d.slug}.zip"
    resp = StreamingHttpResponse(zip_generator(), content_type='application/zip')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp

def thumb(request, slug: str):
    demos = _get_demos()
    d = next((x for x in demos if x.slug == slug), None)
    if not d or not d.thumbnail or not d.thumbnail.exists():
        raise Http404('Thumbnail not found')
    ctype, _ = mimetypes.guess_type(d.thumbnail.name)
    return FileResponse(open(d.thumbnail, 'rb'), content_type=ctype or 'image/png')

@require_POST
def resync(request: HttpRequest):
    use_svn = getattr(settings, 'USE_SVN', False)
    if not use_svn:
        messages.error(request, "SVN mode is disabled (USE_SVN=False).")
        return redirect('gallery')
    try:
        from .utils import sync_all_from_svn
        root_url = getattr(settings, 'SVN_ROOT_URL')
        cache_root = Path(getattr(settings, 'CACHE_ROOT'))
        sync_all_from_svn(root_url, cache_root)
        messages.success(request, "Resynced from SVN successfully.")
    except Exception as e:
        messages.error(request, f"Resync failed: {e}")
    return redirect('gallery')

@require_POST
def resync_demo(request, slug: str):
    use_svn = getattr(settings, 'USE_SVN', False)
    if not use_svn:
        messages.error(request, "SVN mode is disabled (USE_SVN=False).")
        return redirect('detail', slug=slug)
    try:
        from .utils import sync_one_from_svn
        root_url = getattr(settings, 'SVN_ROOT_URL')
        cache_root = Path(getattr(settings, 'CACHE_ROOT'))
        sync_one_from_svn(root_url, cache_root, slug)
        messages.success(request, f"Resynced '{slug}' from SVN successfully.")
    except Exception as e:
        messages.error(request, f"Resync failed for '{slug}': {e}")
    return redirect('detail', slug=slug)