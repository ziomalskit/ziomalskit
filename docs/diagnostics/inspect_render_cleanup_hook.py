"""Read-only check of live renderer completion cleanup hook."""
import pathlib,hashlib
s=pathlib.Path('/workspace/H3_VAST_MOBILE/app/main.py').read_text()
p=s.index('async def render_job(')
end=s.index('def _handle_worker_error',p)
body=s[p:end]
print('LIVE_SHA256',hashlib.sha256(s.encode()).hexdigest(),flush=True)
print('HAS_POST_RENDER_FREE','await prepare_render_model_boundary()' in body,flush=True)
print('RENDER_TAIL',body[-950:],flush=True)
