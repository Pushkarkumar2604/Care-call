import urllib.request
import json

req = urllib.request.Request('https://webhook.site/token', method='POST', headers={'Content-Type': 'application/json', 'User-Agent': 'CareCall/1.0'})
try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        token = data['uuid']
        print('Created webhook.site token:', token)
        
        twiml = '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n  <Say voice="alice" language="en-IN">Hello, this is your CareCall check-in. If you can hear this, your CareCall voice service is working successfully!</Say>\n</Response>'
        
        update_req = urllib.request.Request(
            f'https://webhook.site/token/{token}',
            method='PUT',
            headers={'Content-Type': 'application/json', 'User-Agent': 'CareCall/1.0'},
            data=json.dumps({'default_status': 200, 'default_content_type': 'text/xml', 'default_content': twiml}).encode('utf-8')
        )
        with urllib.request.urlopen(update_req) as u_resp:
            print('Updated webhook response status:', u_resp.status)
            
        test_url = f'https://webhook.site/{token}'
        with urllib.request.urlopen(test_url) as t_resp:
            print('Test URL Content-Type:', t_resp.headers.get('Content-Type'))
            print('Body:\n', t_resp.read().decode('utf-8'))
            print('URL:', test_url)
except Exception as e:
    print('Error:', e)
