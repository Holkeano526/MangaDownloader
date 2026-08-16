import aiohttp
import asyncio
import os

async def test_gofile():
    async with aiohttp.ClientSession() as session:
        async with session.get('https://api.gofile.io/servers') as resp:
            data = await resp.json()
            server = data['data']['servers'][0]['name']
            upload_url = f'https://{server}.gofile.io/uploadFile'
            
        with open('test_upload.pdf', 'wb') as f:
            f.write(b'%PDF-1.4\n')
            
        form_data = aiohttp.FormData(quote_fields=False)
        filename = "みちゆくはな作品集 かけら。 Hitomi.la.pdf"
        ascii_filename = filename.encode('ascii', 'ignore').decode('ascii').replace(" ", "_")
        if not ascii_filename.replace("_", "").replace(".pdf", ""):
            ascii_filename = "manga_download.pdf"
            
        with open('test_upload.pdf', 'rb') as f:
            form_data.add_field('file', f, filename=ascii_filename, content_type='application/pdf')
            async with session.post(upload_url, data=form_data) as upload_resp:
                res = await upload_resp.json()
                print(str(res).encode('utf-8'))

asyncio.run(test_gofile())
