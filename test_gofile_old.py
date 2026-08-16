import aiohttp
import asyncio
import os

async def test_gofile():
    async with aiohttp.ClientSession() as session:
        async with session.get('https://api.gofile.io/servers') as resp:
            data = await resp.json()
            server = data['data']['servers'][0]['name']
            upload_url = f'https://{server}.gofile.io/uploadFile'
            
        form_data = aiohttp.FormData()
        filename = "みちゆくはな作品集 かけら。 Hitomi.la.pdf"
        
        with open('test_upload.pdf', 'rb') as f:
            form_data.add_field('file', f, filename=filename, content_type='application/pdf')
            async with session.post(upload_url, data=form_data) as upload_resp:
                res = await upload_resp.json()
                print(res)

asyncio.run(test_gofile())
