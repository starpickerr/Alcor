"""Local vision reads the guarded file snapshot, including image compression."""
import base64
from io import BytesIO
import random

from PIL import Image
import pytest

from agent.tools.vision.vision import Vision, COMPRESS_THRESHOLD


def protect(path):
    from core.security.protected_paths import ProtectedPathControl
    owner = ProtectedPathControl()
    owner.replace([str(path)], expected_revision=owner.inventory()['revision'])


def test_vision_reads_allowed_image_but_refuses_protected_file_and_alias(tmp_path):
    public = tmp_path / 'ordinary.png'
    Image.new('RGB', (3, 3), 'red').save(public)
    tool = Vision({'cwd': str(tmp_path)})
    data = tool._build_image_content(str(public))['image_url']['url']
    assert base64.b64decode(data.split(',', 1)[1]) == public.read_bytes()
    private = tmp_path / 'private'
    private.mkdir()
    target = private / 'private.png'
    target.write_bytes(public.read_bytes())
    alias = tmp_path / 'alias.png'
    alias.symlink_to(target)
    protect(private)
    for path in (target, alias):
        with pytest.raises(PermissionError):
            tool._build_image_content(str(path))


def test_large_image_compression_does_not_spawn_a_host_process_and_reports_actual_mime(tmp_path, monkeypatch):
    path = tmp_path / 'large.png'
    Image.frombytes('RGB', (1100, 1100), random.Random(42).randbytes(1100 * 1100 * 3)).save(path)
    assert path.stat().st_size > COMPRESS_THRESHOLD
    def no_host_process(*args, **kwargs):
        pytest.fail('Image conversion must not launch an unmanaged host command')
    monkeypatch.setattr('subprocess.run', no_host_process)
    value = Vision()._build_image_content(str(path))['image_url']['url']
    header, encoded = value.split(',', 1)
    assert header == 'data:image/jpeg;base64'
    payload = base64.b64decode(encoded)
    assert len(payload) <= COMPRESS_THRESHOLD
    with Image.open(BytesIO(payload)) as image:
        assert image.format == 'JPEG'
        assert max(image.size) <= 1536


def test_rule_change_after_image_snapshot_stops_vision_before_any_provider_call(tmp_path, monkeypatch):
    folder = tmp_path / 'images'
    folder.mkdir()
    path = folder / 'ordinary.png'
    Image.new('RGB', (3, 3), 'red').save(path)
    tool = Vision()
    monkeypatch.setattr(tool, '_resolve_providers', lambda: [object()])
    def change_after_read(content, mime_type):
        protect(folder)
        return content, mime_type
    def forbidden(*args):
        pytest.fail('An old image snapshot must not reach a provider after protection changes')
    monkeypatch.setattr(tool, '_maybe_compress', change_after_read)
    monkeypatch.setattr(tool, '_call_with_fallback', forbidden)
    with pytest.raises(PermissionError, match='上下文'):
        tool.execute({'image': str(path), 'question': 'Describe this image'})
