from flask import Flask, request, jsonify, Response, stream_with_context
import subprocess, json, sys, re, urllib.request, urllib.parse, urllib.error
app = Flask(__name__)
@app.after_request
def cors(r: Response):
    r.headers["Access-Control-Allow-Origin"]="*"
    r.headers["Access-Control-Allow-Methods"]="GET, POST, OPTIONS"
    r.headers["Access-Control-Allow-Headers"]="Content-Type"
    r.headers["Access-Control-Expose-Headers"]="Content-Disposition, Content-Length"
    return r
@app.route("/health")
def health(): return jsonify({"status":"healthy"})

def ssstik(url):
    data = urllib.parse.urlencode({"id": url, "locale":"id", "tt":"MTUwZThkMg=="}).encode()
    req = urllib.request.Request("https://ssstik.io/abc?url=dl", data=data, headers={
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type":"application/x-www-form-urlencoded",
        "HX-Request":"true",
        "Referer":"https://ssstik.io/id",
        "Origin":"https://ssstik.io",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read().decode(errors="ignore")
    m = re.search(r'href="(https://tikcdn\.io/ssstik/[^"]+)"[^>]*>Tanpa tanda air</a>', html)
    music_m = re.search(r'href="(https://tikcdn\.io/ssstik/m/[^"]+)"', html)
    title_m = re.search(r'<p class="maintext">(.*?)</p>', html, re.S)
    author_m = re.search(r'<h2>(.*?)</h2>', html)
    cover_m = re.search(r'<img class="result_author" src="([^"]+)"', html)
    direct = m.group(1) if m else ""
    if not direct:
        m2 = re.search(r'https://tikcdn\.io/ssstik/\d+\?[^"\']+', html)
        direct = m2.group(0) if m2 else ""
    return {"direct":direct,"title":(title_m.group(1).strip() if title_m else ""),"author":(author_m.group(1).strip() if author_m else ""),"cover":(cover_m.group(1) if cover_m else ""),"music":(music_m.group(1) if music_m else ""),"raw_html_len":len(html)}

@app.route("/api")
def api():
    url = request.args.get("url","").strip()
    if not url: return jsonify({"status":"error","message":"missing url"}),400
    if "tiktok.com" not in url.lower(): return jsonify({"status":"error","message":"bukan link TikTok"}),400
    # ssstik first (no watermark, tikcdn proxy — no Access Denied)
    try:
        s = ssstik(url)
        if s["direct"]:
            return jsonify({"status":"success","source":"ssstik","title":s["title"],"uploader":s["author"],"cover":s["cover"],"play":s["direct"],"hdplay":s["direct"],"wmplay":"","music":s["music"]})
    except Exception as e:
        pass
    # fallback yt-dlp
    for imp in [["--impersonate","chrome"], []]:
        cmd = [sys.executable,"-m","yt_dlp","-j","--no-playlist","--extractor-retries","2","--socket-timeout","20"]+imp+[url]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
            if proc.returncode==0 and proc.stdout.strip():
                try:
                    info=json.loads(proc.stdout.strip().splitlines()[-1])
                    direct = info.get("url") or ""
                    if not direct and info.get("formats"):
                        fmts=[f for f in info["formats"] if f.get("vcodec")!="none" and f.get("url")]
                        if fmts:
                            fmts.sort(key=lambda x:(x.get("height") or 0), reverse=True)
                            direct=fmts[0]["url"]
                    music=""
                    if info.get("formats"):
                        aud=[f for f in info["formats"] if f.get("vcodec")=="none" and f.get("acodec")!="none" and f.get("url")]
                        if aud: music=aud[-1]["url"]
                    cover = info.get("thumbnail") or ""
                    if not cover and info.get("thumbnails"): cover=(info["thumbnails"][-1].get("url") or "")
                    # keep but prefer ssstik next time
                    return jsonify({"status":"success","source":"yt-dlp","title":info.get("title") or info.get("description") or "","uploader":info.get("uploader") or info.get("uploader_id") or "","cover":cover,"play":direct,"hdplay":direct,"wmplay":"","music":music,"id":info.get("id")})
                except: pass
        except: continue
    return jsonify({"status":"error","message":"gagal ambil video — coba refresh / link lain"}),500

@app.route("/download")
def download():
    url = request.args.get("url","")
    filename = request.args.get("filename") or "tiktok.mp4"
    if not url: return "missing url",400
    # allow tikcdn, tiktok cdn, v16 etc
    headers = {
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer":"https://www.tiktok.com/",
        "Accept":"*/*",
    }
    # Range passthrough for resume/preview
    if request.headers.get("Range"):
        headers["Range"]=request.headers.get("Range")
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        return f"upstream {e.code}: {e.reason}", e.code
    except Exception as e:
        return f"proxy error: {e}", 502
    # stream back
    def generate():
        while True:
            chunk = resp.read(64*1024)
            if not chunk: break
            yield chunk
    out_headers = {}
    ctype = resp.headers.get("Content-Type") or "video/mp4"
    clen = resp.headers.get("Content-Length")
    crange = resp.headers.get("Content-Range")
    accept_ranges = resp.headers.get("Accept-Ranges")
    if ctype: out_headers["Content-Type"]=ctype
    if clen: out_headers["Content-Length"]=clen
    if crange: out_headers["Content-Range"]=crange
    if accept_ranges: out_headers["Accept-Ranges"]=accept_ranges
    # force download if filename provided, but allow inline preview
    disp = resp.headers.get("Content-Disposition")
    if not disp:
        out_headers["Content-Disposition"]=f'attachment; filename="{filename}"'
    else:
        out_headers["Content-Disposition"]=disp
    status = resp.status if hasattr(resp,"status") else 200
    return Response(stream_with_context(generate()), status=status, headers=out_headers)

@app.route("/mp3")
def mp3_proxy():
    # alias to download with audio mime
    return download()

# Vercel serves / as static index.html — remove Flask "/" to avoid shadowing
# Flask handles /health /api /download /mp3 via rewrites -> /api/index (see vercel.json)
if __name__=="__main__":
    port=int(os.environ.get("PORT","5001"))
    print(f"tiktok yt-dlp+ssstik+proxy server http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, threaded=True)
