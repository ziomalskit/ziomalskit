"""Read-only local latency samples; never use or print account credentials."""
import time,urllib.request,urllib.error,statistics
urls=[
 ("panel","http://127.0.0.1:7860/"),
 ("render_queue","http://127.0.0.1:8188/queue"),
 ("prompt_queue","http://127.0.0.1:8189/queue"),
 ("render_health","http://127.0.0.1:8188/system_stats"),
 ("prompt_health","http://127.0.0.1:8189/system_stats"),
]
for label,url in urls:
    measurements=[]
    codes=[]
    for i in range(3):
        start=time.monotonic()
        try:
            with urllib.request.urlopen(url,timeout=8) as response:
                code=response.status
                response.read(32768)
        except urllib.error.HTTPError as error:
            code=error.code
            error.read(128)
        except Exception as error:
            code=type(error).__name__
        measurements.append(round((time.monotonic()-start)*1000,1))
        codes.append(code)
    print("LATENCY",label,"median_ms",round(statistics.median(measurements),1),
           "max_ms",max(measurements),"codes",codes,flush=True)
