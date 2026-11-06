"""Loopback backend and tiny integrity fixtures for functional journeys.

Only model execution is simulated. The controller, HTTP, disk snapshots,
adapters, integrity checks, submission UUIDs and result proxy remain real.
Set AJ_E2E_COMFY_PYTHON to exercise installed comfy-cli subprocesses too;
otherwise only its preview/validation/watch boundary uses CPU fixtures.
"""
import base64
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit
import wave

from h3.app.artifacts import verify_file
from h3.app.production import immutable_registry, read_bridge, read_loras
from h3.app.workflow_conversion import validate_api_prompt
from tests.helpers import production_fixture_texts
from tests.test_production_api import production_catalog
from tests.test_workflows import exact_convert

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')


def wav():
    output = io.BytesIO()
    with wave.open(output, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(bytes(32000))
    return output.getvalue()


def stage_models(module):
    """Rewrite trusted metadata ONLY in load_controller's disposable copy."""
    path = module.CONFIG / 'models_manifest.json'
    manifest = json.loads(path.read_text())
    by_name = {}
    for item in manifest['artifacts']:
        data = ('INERT CPU FIXTURE: ' + item['id']).encode()
        item.update(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        target = module.COMFY_MODELS_DIR / item['destination']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        receipt = {**item, **verify_file(module.COMFY_MODELS_DIR, item['destination'], item)}
        verified = module.COMFY_MODELS_DIR / '.verification' / (item['id'] + '.json')
        verified.parent.mkdir(exist_ok=True)
        verified.write_text(json.dumps(receipt))
        by_name[target.name] = item
    path.write_text(json.dumps(manifest))
    path = module.CONFIG / 'lora_registry.json'
    registry = json.loads(path.read_text())
    for entry in [*registry['loras'], registry['conditioning_bridge']]:
        if entry['filename'] in by_name:
            item = by_name[entry['filename']]
            entry.update(size_bytes=item['size_bytes'], sha256=item['sha256'])
    path.write_text(json.dumps(registry))
    module.LORA_REGISTRY = immutable_registry(read_loras(path))
    module.CONDITIONING_BRIDGE = immutable_registry(read_bridge(path))
    module.COMFY_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    (module.COMFY_INPUT_DIR / '__h3_silence_1s.wav').write_bytes(wav())


class Backend:
    def __init__(self, module, service):
        self.module, self.service = module, service
        self.catalog = production_catalog()
        self.calls, self.submissions, self.histories = [], [], {}
        self.durable_arms = []
        self.failure = None
        self.epoch = '1' * 32
        self.media = b'fixture MP4 bytes; replace with ffmpeg for playback checks'
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                backend.handle(self)

            def do_POST(self):
                backend.handle(self)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def handle(self, request):
        path = urlsplit(request.path).path
        body = json.loads(request.rfile.read(int(request.headers['Content-Length']))) if request.headers.get('Content-Length') else {}
        self.calls.append((request.command, path))
        code, value = 200, {}
        if path == '/object_info':
            value = self.catalog
        elif path == '/aj/execution_protocol':
            value = {'protocol': 'aj-terminal-v1', 'quiesced': False}
            if self.failure == 'unavailable':
                code = 503
        elif path == '/aj/service_epoch':
            value = {'service_epoch': self.epoch}
        elif path == '/prompt':
            saved = json.loads(self.module.QUEUE_FILE.read_text())['queue']
            self.durable_arms.append(any(j.get(self.service + '_prompt_id') == body['prompt_id'] and
                j.get(self.service + '_submission_state') == 'armed' for j in saved))
            if self.failure == 'reject':
                code, value = 400, {'error': 'fixture rejection'}
            else:
                self.submissions.append(body)
                self.histories[body['prompt_id']] = self.history(body)
                if self.failure == 'lost_ack':
                    request.close_connection = True
                    return
                value = {'prompt_id': body['prompt_id']}
        elif path.startswith('/history/'):
            key = path.rsplit('/', 1)[1]
            value = {key: self.histories[key]} if key in self.histories else {}
        elif path == '/history':
            value = self.histories
        elif path.startswith('/aj/results/'):
            key = path.rsplit('/', 1)[1]
            value = self.histories.get(key, {'protocol': 'aj-terminal-v1', 'prompt_id': key, 'state': 'unknown'})
        elif path == '/queue':
            value = {'queue_running': [], 'queue_pending': []}
        elif path == '/view':
            data = self.media
            headers = {'content-type': 'video/mp4', 'accept-ranges': 'bytes'}
            if request.headers.get('Range'):
                start, end = request.headers['Range'].removeprefix('bytes=').split('-')
                start, end = int(start), int(end) if end else len(data) - 1
                headers['content-range'] = f'bytes {start}-{end}/{len(data)}'
                data, code = data[start:end + 1], 206
            request.send_response(code)
            for key, value in headers.items():
                request.send_header(key, value)
            request.send_header('content-length', str(len(data)))
            request.end_headers()
            request.wfile.write(data)
            return
        elif path not in {'/free', '/system_stats'}:
            code, value = 404, {'unexpected_fixture_route': path}
        data = json.dumps(value).encode()
        request.send_response(code)
        request.send_header('content-type', 'application/json')
        request.send_header('content-length', str(len(data)))
        request.end_headers()
        request.wfile.write(data)

    def history(self, body):
        graph = copy.deepcopy(body['prompt'])
        outputs = {}
        if self.service == 'prompt':
            job = next(j for j in self.module.queue if j.get('prompt_prompt_id') == body['prompt_id'])
            plan, final = production_fixture_texts(job['context'], 'A person walks across a sunlit room')
            if self.failure == 'bad_time':
                plan = plan.replace('00:00.000-', '00:01.000-')
            if self.failure == 'bad_final':
                final = 'unfinished model response'
            values = {'visual_facts': 'Reference 1: adult in a sunlit room.',
                      'expanded_intent': 'A continuous walk.', 'reference_map': 'Six uploaded references retained.',
                      'creative_plan': plan, 'final_h3_prompt': final}
            capture = self.module.job_workflow_adapter(job).descriptor['api']['captures']
            for key, node in graph.items():
                field = next((field for field, title in capture.items() if node.get('_meta', {}).get('title') == title), None)
                if field:
                    outputs[key] = {'text': [values[field]]}
                if key == '5732':
                    outputs[key] = {'text': [final]}
        elif self.failure != 'no_video':
            outputs = {'7331': {'images': [{'filename': 'fixture.mp4', 'subfolder': 'video', 'type': 'output'}]}}
        status = {'status_str': 'success', 'completed': True, 'messages': []}
        if self.failure == 'interrupted':
            outputs = {key: value for key, value in outputs.items() if
                graph[key].get('_meta', {}).get('title') == 'STEP 0 — JoyCaption Visual Facts / Output'}
            status = {'status_str': 'error', 'completed': False, 'messages': [['execution_interrupted', {}]]}
        return {'status': status,
                'prompt': [0, body['prompt_id'], graph, {'client_id': body['client_id']}, list(outputs)], 'outputs': outputs}


async def fixture_cli(module, backend, service, *args):
    """Only used by normal CPU suite; review can select the installed CLI."""
    if args[0] == 'run':
        path = Path(args[args.index('--workflow') + 1])
        return {'data': {'status': 'preview', 'prompt': exact_convert(json.loads(path.read_text()), backend.catalog)}}
    path = Path(args[args.index('--workflow') + 1])
    validate_api_prompt(json.loads(path.read_text()), backend.catalog)
    return {'data': {'valid': True, 'error_count': 0}}
