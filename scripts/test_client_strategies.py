import yt_dlp

clients_to_test = [
    ['tv'],
    ['tv_embedded'],
    ['mweb'],
    ['ios'],
    ['android'],
    ['web'],
    ['web_embedded']
]

for c in clients_to_test:
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extractor_args': {'youtube': {'player_client': c}}
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info('https://www.youtube.com/watch?v=yr7VY7_oYW4', download=False)
            t = info.get('title')
            fmts = len(info.get('formats', []))
            print(f"Client {c}: SUCCESS! Title: {t}, Formats: {fmts}")
    except Exception as e:
        err = str(e).split('\n')[0]
        print(f"Client {c}: FAILED - {err[:100]}")
