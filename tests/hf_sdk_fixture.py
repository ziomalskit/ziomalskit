"""Tiny CPU SDK transport fixture: never accesses a provider or real model."""
from contextlib import contextmanager
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


@contextmanager
def sdk_fixture(item, body):
    hub = ModuleType('huggingface_hub')
    transport = ModuleType('huggingface_hub.file_download')
    utils = ModuleType('huggingface_hub.utils')
    hub.hf_hub_url = Mock(return_value='https://huggingface.co/fixture/resolve/revision/model.gguf')
    metadata = SimpleNamespace(commit_hash=item['revision'], size=len(body), location='https://cdn.fixture/model')
    hub.get_hf_file_metadata = Mock(return_value=metadata)
    def receive(url, stream, *, resume_size, **kwargs):
        stream.write(body[resume_size:])
    transport.http_get = Mock(side_effect=receive)
    utils.build_hf_headers = Mock(return_value={'authorization': 'Bearer CPU_SENTINEL'})
    with patch.dict(sys.modules, {'huggingface_hub': hub, 'huggingface_hub.file_download': transport,
                                  'huggingface_hub.utils': utils}):
        yield SimpleNamespace(hub=hub, transport=transport, utils=utils, metadata=metadata)
