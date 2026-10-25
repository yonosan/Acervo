import base64
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory()
os.environ.update(ACERVO_DESKTOP='1', DATA_DIR=TEMP.name, APP_PASSWORD='senha-exclusiva-para-testes', APP_ORIGIN='http://127.0.0.1:18765', ACERVO_BOOT_TICKET='bootstrap-exclusivo-de-teste')
import app
import catalog
import yt_engine
import covers
import spotify_browser as spotify
from fastapi.testclient import TestClient
from security import SourceError


class FakeSpotifyPage:
    url='https://open.spotify.com/collection/tracks'
    def __init__(self,rows):self.rows=rows
    def evaluate(self,script,*args):
        if script==spotify.READ_ROWS:return {'rows':self.rows,'total':len(self.rows)}
        return {'found':True,'moved':False,'bottom':True}
    def wait_for_timeout(self,_):pass


class SpotifyTests(unittest.TestCase):
    def test_collect_visible_metadata_without_api(self):
        import threading
        rows=[{'id':'spotify-1234567890123456789012','title':'Minha música','artist':'Meu artista','album':'Álbum','duration':200}]
        saved=[]
        result=spotify.collect_tracks(FakeSpotifyPage(rows),saved.append,threading.Event(),lambda *_:None)
        self.assertEqual(result['count'],1)
        self.assertTrue(result['complete'])
        self.assertEqual(saved[0]['album'],'Álbum')
        self.assertEqual(saved[0]['state'],'Precisa revisar')

    def test_login_page_is_not_read(self):
        import threading
        page=FakeSpotifyPage([])
        page.url='https://accounts.spotify.com/login'
        with self.assertRaises(SourceError):spotify.collect_tracks(page,lambda _:None,threading.Event(),lambda *_:None)

    def test_empty_page_requests_manual_login(self):
        import threading
        with self.assertRaisesRegex(SourceError,'não conseguiu reconhecer'):
            spotify.collect_tracks(FakeSpotifyPage([]),lambda _:None,threading.Event(),lambda *_:None)

    def test_selects_liked_songs_in_new_login_tab(self):
        from types import SimpleNamespace
        initial=SimpleNamespace(url='https://open.spotify.com/',is_closed=lambda:False)
        login=SimpleNamespace(url='https://accounts.spotify.com/login',is_closed=lambda:False)
        library=SimpleNamespace(url='https://open.spotify.com/intl-pt/collection/tracks',is_closed=lambda:False)
        context=SimpleNamespace(pages=[initial,login,library])
        self.assertIs(spotify.current_spotify_page(context,navigate=True),library)

    def test_closed_first_tab_keeps_new_library_tab(self):
        from types import SimpleNamespace
        initial=SimpleNamespace(url='about:blank',is_closed=lambda:True)
        library=SimpleNamespace(url='https://open.spotify.com/collection/tracks',is_closed=lambda:False)
        self.assertIs(spotify.current_spotify_page(SimpleNamespace(pages=[initial,library])),library)

    def test_home_page_navigates_to_liked_songs(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        initial=SimpleNamespace(url='https://open.spotify.com/intl-pt',is_closed=lambda:False,goto=Mock())
        self.assertIs(spotify.current_spotify_page(SimpleNamespace(pages=[initial]),navigate=True),initial)
        self.assertEqual(initial.goto.call_args.args[0],'https://open.spotify.com/collection/tracks')

    def test_waits_for_slow_library_to_render(self):
        import threading
        row={'id':'spotify-1234567890123456789012','title':'A','artist':'B'}
        class LoadingPage(FakeSpotifyPage):
            reads=0
            def evaluate(self,script,*args):
                if script==spotify.READ_ROWS:
                    self.reads+=1
                    return {'rows':[] if self.reads<4 else self.rows,'total':1}
                return super().evaluate(script,*args)
        saved=[]
        result=spotify.collect_tracks(LoadingPage([row]),saved.append,threading.Event(),lambda *_:None)
        self.assertEqual(result['count'],1)
        self.assertEqual(len(saved),1)

    def test_cancel_preserves_already_read_rows(self):
        import threading
        stop=threading.Event()
        saved=[]
        page=FakeSpotifyPage([{'id':'spotify-1234567890123456789012','title':'A','artist':'B'}])
        with self.assertRaisesRegex(SourceError,'1 música'):
            spotify.collect_tracks(page,saved.append,stop,lambda *_:stop.set())
        self.assertEqual(len(saved),1)

    def test_free_mode_requires_no_client_id(self):
        with patch.object(spotify.SpotifyBrowser,'_run'):
            sp=spotify.SpotifyBrowser(Path(TEMP.name)/'browser',lambda _:None,lambda *_:None)
            self.assertFalse(sp.status()['requires_premium'])
            self.assertNotIn('client_id',sp.status())
            with self.assertRaises(SourceError):sp.start_sync()
            sp.close()


class MatchingTests(unittest.TestCase):
    def test_windows_instance_reopens_existing_owner_and_releases_on_exit(self):
        from desktop_runtime import SingleInstance
        directory=Path(TEMP.name)/'instance-test'
        first=SingleInstance(directory)
        second=None
        try:
            self.assertFalse(first.existing)
            second=SingleInstance(directory)
            self.assertTrue(second.existing)
            self.assertEqual(first.kernel.WaitForSingleObject(first.event,1000),0)
        finally:
            if second:second.close()
            first.close()
        fresh=SingleInstance(directory)
        try:self.assertFalse(fresh.existing)
        finally:fresh.close()

    def test_background_close_can_hide_or_exit_without_stopping_downloads(self):
        from desktop_runtime import BackgroundWindow
        from types import SimpleNamespace
        from unittest.mock import Mock
        import threading
        class Event:
            def __iadd__(self,handler):return self
        fake=SimpleNamespace(DESKTOP_RUNTIME=dict(available=True,background=False,suspended=False),BACKGROUND=threading.Event(),
                             config_read=lambda key:True)
        window=SimpleNamespace(events=SimpleNamespace(closing=Event(),minimized=Event(),loaded=Event()))
        instance=SimpleNamespace(wake=Mock())
        runtime=BackgroundWindow(window,fake,instance,Path('not-needed.ico'))
        runtime.ui=Mock()
        self.assertFalse(runtime.on_closing())
        runtime.ui.assert_called_once_with(runtime.hide_ui,later=True)
        self.assertFalse(runtime.exiting)
        fake.config_read=lambda key:False
        self.assertTrue(runtime.on_closing())
        self.assertTrue(runtime.exiting)
        self.assertFalse(fake.DESKTOP_RUNTIME['available'])
        instance.wake.assert_called()

    def test_idle_worker_blocks_without_polling(self):
        from unittest.mock import Mock
        work=Mock()
        work.get.return_value=None
        with patch.object(app,'JOBS',work):app.worker()
        work.get.assert_called_once_with()
        work.task_done.assert_called_once_with()

    def test_spotify_idle_does_not_start_browser_driver(self):
        with patch('playwright.sync_api.sync_playwright') as driver:
            reader=spotify.SpotifyBrowser(Path(TEMP.name)/'idle-browser',lambda _:None,lambda *_:None)
            try:
                time.sleep(.1)
                driver.assert_not_called()
            finally:reader.close()
            self.assertFalse(reader.thread.is_alive())
    def test_playlist_ignores_duplicates_private_items_and_foreign_urls(self):
        entries=yt_engine.playlist_entries({'entries':[
            {'id':'abcdefghijk','title':'Faixa','url':'https://127.0.0.1/privado'},
            {'id':'abcdefghijk','title':'Repetida'},None,
            {'id':'zyxwvutsrqp','availability':'private'}, {'id':'../../local'},
            {'id':'qwertyuiopa','is_live':True}, {'id':'12345678901','title':'Outra'}]})
        self.assertEqual(len(entries),2)
        self.assertEqual(entries[0]['url'],'https://www.youtube.com/watch?v=abcdefghijk')
        self.assertEqual(entries[0]['formats'],[])
        with self.assertRaisesRegex(ValueError,'limite'):
            yt_engine.playlist_entries({'entries':[{'id':'abcdefghijk'},{'id':'12345678901'}]},limit=1)

    def test_other_video_codecs_offer_conversion_without_drm(self):
        formats=yt_engine.available_formats({'formats':[
            dict(format_id='audio',ext='webm',vcodec='none',acodec='opus',abr=128,protocol='https'),
            dict(format_id='video',ext='webm',vcodec='vp9',acodec='none',height=1080,protocol='https'),
            dict(format_id='drm',ext='webm',vcodec='vp9',acodec='opus',height=720,protocol='https',has_drm=True)]})
        self.assertEqual([f['id'] for f in formats],['audio','video+audio'])
        self.assertTrue(formats[1]['transcode'])
        self.assertEqual(formats[1]['ext'],'mkv')

    def test_mp4_uses_compatible_video_audio_without_drm(self):
        info={'formats':[
            {'format_id':'a','ext':'m4a','vcodec':'none','acodec':'mp4a.40.2','protocol':'https','abr':128,'filesize':100},
            {'format_id':'v','ext':'mp4','vcodec':'avc1.4d','acodec':'none','protocol':'https','height':720,'filesize':400},
            {'format_id':'drm','ext':'mp4','vcodec':'avc1','acodec':'none','protocol':'https','height':720,'has_drm':True},
            {'format_id':'unsafe','ext':'mp4','vcodec':'avc1','acodec':'none','protocol':'http','height':720},
            {'format_id':'4k','ext':'mp4','vcodec':'avc1','acodec':'none','protocol':'https','height':2160}]}
        formats=yt_engine.available_formats(info)
        self.assertEqual([f['id'] for f in formats],['a','v+a'])
        self.assertEqual(formats[1]['size'],500)
        self.assertEqual(formats[1]['kind'],'video')
        self.assertEqual(app.validate_format({'source':'YouTube','formats':formats},'v+a','mp3',0)['kind'],'video')
        self.assertEqual(app.validate_format({'source':'YouTube','formats':formats},'a','mp4',128)['kind'],'audio')
        with self.assertRaises(SourceError):app.validate_format({'source':'YouTube','formats':formats},'v+a','original',128)

    def test_windows_worker_protocol_is_always_utf8(self):
        import desktop
        incoming=io.TextIOWrapper(io.BytesIO(),encoding='cp1252')
        buffer=io.BytesIO()
        outgoing=io.TextIOWrapper(buffer,encoding='cp1252')
        with patch('sys.stdin',incoming),patch('sys.stdout',outgoing):
            desktop.restore_worker_pipes()
            print(json.dumps({'title':'Canção, coração e emoção'},ensure_ascii=False))
            sys.stdout.flush()
            self.assertEqual(sys.stdin.encoding,'utf-8')
        self.assertEqual(json.loads(buffer.getvalue().decode('utf-8'))['title'],'Canção, coração e emoção')

    def test_youtube_official_title_with_missing_album(self):
        track={'title':'Gimme Love','artist':'Joji','album':'Nectar','duration':214}
        candidate={'title':'Joji - Gimme Love (Official Video)','artist':'JojiVEVO','source':'YouTube','duration':215}
        result=catalog.match(track,candidate)
        self.assertTrue(result['confident'],result)
        self.assertIn('Álbum não informado',result['reason'])

    def test_topic_channel_matches_original_audio(self):
        track={'title':'Minha Música','artist':'Artista','duration':180}
        candidate={'title':'Minha Música (Official Audio)','artist':'Artista - Topic','source':'YouTube','duration':180}
        self.assertTrue(catalog.match(track,candidate)['confident'])

    def test_versions_artist_album_and_duration_still_require_review(self):
        track={'title':'Gimme Love','artist':'Joji','album':'Nectar','duration':214}
        original={'title':'Joji - Gimme Love (Official Video)','artist':'Joji','source':'YouTube','duration':214}
        changes=[{'title':'Joji - Gimme Love (Live)'},{'title':'Joji - Gimme Love (Cover)'},
                 {'title':'Joji - Gimme Love (Remix)'},{'title':'Joji - Gimme Love (Remastered)'},
                 {'artist':'Outra banda'},{'duration':245},{'duration':None},{'album':'Outro álbum'}]
        for change in changes:
            result=catalog.match(track,{**original,**change})
            self.assertFalse(result['confident'],(change,result))


class WindowsFlowTests(unittest.TestCase):
    def setUp(self):
        app.RATES.clear()
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(app.app,base_url=app.ORIGIN)
        cls.client=cls.client_context.__enter__()
        cls.headers={'Origin':app.ORIGIN}
        cls.output=Path(TEMP.name)/'Destino'
        cls.output.mkdir()
        app.config_write('output_folder',str(cls.output))
        cls.fixture=Path(TEMP.name)/'fixture.wav'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1',str(cls.fixture)],check=True)
        cls.wav=cls.fixture.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None,None,None)

    def post(self,path,data):
        return self.client.post('/api/'+path,json=data,headers=self.headers)

    def wait(self,identifier):
        deadline=time.time()+15
        while time.time()<deadline:
            item=app.get('job',identifier)
            if item['state'] not in app.ACTIVE and not (item['state']=='Concluída' and item.get('file')):return item
            time.sleep(.05)
        self.fail('Tarefa não concluiu')

    def test_01_bootstrap_without_user_password(self):
        self.assertEqual(self.client.get('/api/state').status_code,401)
        self.assertEqual(self.client.get('/desktop/boot/wrong').status_code,404)
        self.assertEqual(self.client.get('/desktop/boot/bootstrap-exclusivo-de-teste').status_code,200)
        self.assertEqual(self.client.get('/desktop/boot/bootstrap-exclusivo-de-teste').status_code,404)
        state=self.client.get('/api/state').json()
        self.assertTrue(state['desktop'])
        self.assertEqual(state['output_folder'],str(self.output))
        self.assertEqual(self.post('import',{'filename':'list.json','content':'[]'}).status_code,400)

    def test_02_save_file_without_overwriting_existing(self):
        first=Path(app.save_to_folder(self.fixture,self.output,'Artista - Música.wav'))
        second=Path(app.save_to_folder(self.fixture,self.output,'Artista - Música.wav'))
        self.assertNotEqual(first,second)
        self.assertEqual(first.read_bytes(),second.read_bytes())

    def test_03_download_convert_persist_and_play(self):
        media=app.remember({'url':'https://example.com/test.wav','title':'Teste','artist':'Acervo','source':'Arquivo direto','formats':[{'id':'original','ext':'wav','abr':None,'size':len(self.wav)}]})
        class Connection:
            def close(self):pass
        class Response(io.BytesIO):
            def getheader(self,key,default=None):return str(len(WindowsFlowTests.wav)) if key=='Content-Length' else default
        body=dict(media_id=media['id'],format_id='original',title='Teste',artist='Acervo',output='mp3',authorized=True)
        with patch('app.open_public',side_effect=lambda _:(Connection(),Response(self.wav))):
            response=self.post('jobs',body)
            self.assertEqual(response.status_code,200,response.text)
            identifier=response.json()['id']
            item=self.wait(identifier)
        self.assertEqual(item['state'],'Concluída',item)
        saved=Path(item['saved_path'])
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.parent,self.output)
        self.assertFalse((Path(TEMP.name)/'files'/identifier).exists())
        app.patch_job(identifier,expires=time.time()-1)
        app.cleanup_once()
        response=self.client.get('/api/jobs/'+identifier+'/file?play=true')
        self.assertEqual(response.status_code,200)
        self.assertIn('inline',response.headers['content-disposition'])
        self.assertEqual(response.content,saved.read_bytes())
        app.probe(saved)

    def test_04_spotify_routes_do_not_require_secrets(self):
        state=self.client.get('/api/state').json()
        self.assertFalse(state['spotify']['requires_premium'])
        self.assertEqual(self.post('spotify/configure',{'client_id':'bad'}).status_code,404)
        with patch.object(app.SPOTIFY,'begin'):
            self.assertEqual(self.post('spotify/connect',{}).status_code,200)
        self.assertEqual(self.post('spotify/sync',{}).status_code,400)

    def test_05_preserve_review_when_sync_updates_metadata(self):
        item={'id':'spotify-existing','title':'A','artist':'B','state':'Encontrada','media_id':'known','candidates':[{'id':'known'}]}
        app.save('track',item)
        app.save_spotify_track({**item,'title':'A atualizado','state':'Precisa revisar','media_id':None,'candidates':[]})
        result=app.get('track',item['id'])
        self.assertEqual(result['state'],'Encontrada')
        self.assertEqual(result['media_id'],'known')
        self.assertEqual(result['title'],'A atualizado')

    def test_06_all_1062_tracks_enter_one_queue_and_cancel_without_search(self):
        ids=[]
        for i in range(1062):
            identifier=f'bulk-{i}'
            app.save('track',dict(id=identifier,title=f'Música {i}',artist='Acervo',duration=1,state='Precisa revisar',candidates=[],media_id=None))
            ids.append(identifier)
        body=dict(track_ids=ids,source='youtube',output='mp3',authorized=False)
        self.assertEqual(self.post('library/download',body).status_code,400)
        with patch('app.process_job'):
            body['authorized']=True
            response=self.post('library/download',body)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['count'],1062)
            self.assertEqual(self.post('library/download',body).json()['count'],0)
            snapshot=self.client.get('/api/state').json()
            self.assertEqual(sum(j['state']=='Na fila' for j in snapshot['jobs']),1062)
            self.assertEqual(snapshot['limits']['workers'],2)
            self.assertEqual(self.post('jobs/cancel-all',{}).json()['count'],1062)
            app.JOBS.join()
        self.assertFalse(any(j['state'] in app.ACTIVE for j in app.records('job')))

    def test_07_background_search_saves_mp3_and_leaves_cover_for_review(self):
        for identifier,title in [('auto-ok','Minha faixa'),('auto-review','Minha versão')]:
            app.save('track',dict(id=identifier,title=title,artist='Acervo',duration=1,state='Precisa revisar',candidates=[],media_id=None))
        def candidates(title,artist):
            return ([dict(url='https://example.com/'+title+'.wav',title=title+' (Cover)' if title=='Minha versão' else title,
                          artist=artist,duration=1,source='Internet Archive',formats=[dict(id='original',ext='wav',size=len(self.wav),abr=None)])],[])
        class Connection:
            def close(self):pass
        class Response(io.BytesIO):
            def getheader(self,key,default=None):return str(len(WindowsFlowTests.wav)) if key=='Content-Length' else default
        with patch('app.archive_search',side_effect=candidates),patch('app.open_public',side_effect=lambda _:(Connection(),Response(self.wav))):
            response=self.post('library/download',dict(track_ids=['auto-ok','auto-review'],source='archive',output='mp3',authorized=True))
            self.assertEqual(response.json()['count'],2)
            first=self.wait(app.get('track','auto-ok')['job_id'])
            second=self.wait(app.get('track','auto-review')['job_id'])
        self.assertEqual(first['state'],'Concluída',first)
        self.assertEqual(Path(first['saved_path']).suffix,'.mp3')
        app.probe(first['saved_path'])
        self.assertEqual(second['state'],'Precisa revisar',second)
        self.assertFalse(second.get('saved_path'))
        self.assertTrue(app.get('track','auto-review')['candidates'])

    def test_08_mp4_real_validation_and_save(self):
        video=Path(TEMP.name)/'fixture.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=160x90:d=1','-i',str(self.fixture),
                        '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(video)],check=True)
        media=app.remember(dict(url='https://www.youtube.com/watch?v=testmp4',title='Vídeo teste',artist='Acervo',duration=1,source='YouTube',
                                formats=[dict(id='v+a',ext='mp4',kind='video',height=720,size=video.stat().st_size,abr=None)]))
        def engine(request,job_id=None):
            import shutil
            shutil.copy2(video,Path(request['directory'])/'source.mp4')
            return {'ext':'mp4'}
        with patch('app.run_engine',side_effect=engine):
            response=self.post('jobs',dict(media_id=media['id'],format_id='v+a',title='Vídeo teste',artist='Acervo',output='mp4',authorized=True))
            self.assertEqual(response.status_code,200,response.text)
            saved=self.wait(response.json()['id'])
            app.save('track',dict(id='mp4-bulk',title='Vídeo em lote',artist='Acervo',state='Encontrada',media_id=media['id'],candidates=[media],duration=1))
            batch=self.post('library/download',dict(track_ids=['mp4-bulk'],source='youtube',output='mp4',authorized=True))
            self.assertEqual(batch.json()['count'],1)
            automatic=self.wait(app.get('track','mp4-bulk')['job_id'])
            self.assertEqual(automatic['state'],'Concluída',automatic)
            app.probe(automatic['saved_path'],video=True)
        self.assertEqual(saved['state'],'Concluída',saved)
        self.assertEqual(Path(saved['saved_path']).suffix,'.mp4')
        app.probe(saved['saved_path'],video=True)
        with self.assertRaises(SourceError):app.probe(saved['saved_path'])
        self.assertIn('video/mp4',self.client.get('/api/jobs/'+saved['id']+'/file?play=true').headers['content-type'])

    def test_08_review_can_restore_candidate_after_metadata_cache_cleanup(self):
        candidate=app.remember(dict(url='https://example.org/revisar.wav',title='Revisar',artist='Acervo',source='Arquivo direto',formats=[]))
        app.DB.execute("DELETE FROM records WHERE kind='media' AND id=?",(candidate['id'],));app.DB.commit()
        app.save('track',dict(id='pruned-track',title='Revisar',artist='Acervo',state='Precisa revisar',candidates=[candidate],media_id=None))
        response=self.post('library/pruned-track/select',{'media_id':candidate['id']})
        self.assertEqual(response.status_code,200,response.text)
        restored=app.get('track','pruned-track')['media_id']
        self.assertEqual(app.get('media',restored)['url'],candidate['url'])

    def test_09_pending_downloads_survive_restart(self):
        tracks=[dict(id=f'resume-{i}',title='Retomar',artist='Acervo',duration=1,state='Precisa revisar',candidates=[],media_id=None) for i in range(1062)]
        with app.STATE:
            app.DB.executemany('INSERT OR REPLACE INTO records VALUES (?,?,?,?)',[('track',t['id'],json.dumps(t),time.time()) for t in tracks]);app.DB.commit()
        with patch('app.process_job'):
            response=self.post('library/download',dict(track_ids=[t['id'] for t in tracks],source='youtube',output='mp3',authorized=True))
            self.assertEqual(response.json()['count'],1062)
            app.JOBS.join()
            identifiers={app.get('track',t['id'])['job_id'] for t in tracks}
            self.client_context.__exit__(None,None,None)
        with patch('app.process_job') as process:
            self.client_context=TestClient(app.app,base_url=app.ORIGIN)
            started=time.monotonic()
            self.client=self.client_context.__enter__()
            app.JOBS.join()
            self.assertLess(time.monotonic()-started,10,'A restauração não deve regravar a fila inteira')
            self.assertEqual({call.args[0] for call in process.call_args_list},identifiers)
            for identifier in identifiers:
                item=app.get('job',identifier);item.update(state='Cancelada',cancel=True);app.save('job',item)
        type(self).client_context=self.client_context
        type(self).client=self.client

    def test_10_cover_cache_resize_and_unsafe_image_rejection(self):
        jpg=Path(TEMP.name)/'capa.jpg'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=green:s=640x640','-frames:v','1','-threads','1',str(jpg)],check=True)
        directory=Path(TEMP.name)/'cover-test'
        item=dict(id='cover-test',thumbnail='https://example.org/capa.jpg')
        with patch('covers.read_public',return_value=jpg.read_bytes()) as download:
            first=covers.get_cover(directory,item)
            self.assertTrue(first.is_file())
            self.assertEqual(first,covers.get_cover(directory,item))
            download.assert_called_once()
        with patch('covers.read_public',return_value=b'<svg onload="bad()"/>'):
            self.assertIsNone(covers.get_cover(directory,dict(id='invalid',thumbnail='https://example.org/invalid.svg')))
        with self.assertRaises(SourceError):
            covers.read_public('http://127.0.0.1/internal',100)

    def test_11_cover_embedded_in_mp3_and_mp4_without_changing_recording(self):
        from mutagen.id3 import ID3
        from mutagen.mp4 import MP4
        for ext,args in [('mp3',['-c:a','libmp3lame']),('m4a',['-c:a','aac'])]:
            file=Path(TEMP.name)/('capa-audio.'+ext)
            subprocess.run(['ffmpeg','-v','error','-i',str(self.fixture),*args,str(file)],check=True)
            def pcm():
                return subprocess.run(['ffmpeg','-v','error','-i',str(file),'-map','0:a:0','-f','s16le','-'],capture_output=True,check=True).stdout
            before=pcm()
            self.assertTrue(covers.embed_existing(file,Path(TEMP.name)/'capa.jpg',app.probe))
            self.assertEqual(pcm(),before)
            if ext=='mp3':self.assertTrue(ID3(file).getall('APIC'))
            else:self.assertTrue(MP4(file)['covr'])
        video=Path(TEMP.name)/'fixture.mp4'
        self.assertTrue(covers.embed_existing(video,Path(TEMP.name)/'capa.jpg',lambda p:app.probe(p,video=True)))
        self.assertTrue(MP4(video)['covr'])

    def test_12_failed_cover_update_preserves_original_file(self):
        original=Path(TEMP.name)/'capa-audio.mp3'
        before=original.read_bytes()
        def reject(_):raise SourceError('Falha de validação controlada')
        with self.assertRaises(SourceError):covers.embed_existing(original,Path(TEMP.name)/'capa.jpg',reject)
        self.assertEqual(original.read_bytes(),before)
        self.assertFalse(list(original.parent.glob('.acervo-capa-*')))

    def test_13_cover_endpoint_requires_session_and_uses_private_cache(self):
        self.post('login',{'password':app.PASSWORD})
        self.assertEqual(TestClient(app.app,base_url=app.ORIGIN).get('/api/covers/track/anything').status_code,401)
        with patch('app.cover_for',return_value=Path(TEMP.name)/'capa.jpg'):
            response=self.client.get('/api/covers/track/known')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['content-type'],'image/jpeg')
        self.assertIn('private',response.headers['cache-control'])

    def move_fixture(self,identifier):
        source=self.output/(identifier+' - Música.wav')
        source.write_bytes(self.wav)
        app.save('job',dict(id=identifier,title='Música',artist='Acervo',output='original',state='Concluída',created=app.now(),saved_path=str(source),output_folder=str(self.output),progress=100,bytes=len(self.wav),size=len(self.wav)))
        return source

    def test_14_move_file_updates_history_without_overwriting(self):
        source=self.move_fixture('move-collision')
        folder=Path(TEMP.name)/'Pasta nova'
        folder.mkdir()
        existing=folder/source.name
        existing.write_bytes(b'arquivo existente preservado')
        app.config_write('move_folder',str(folder))
        response=self.post('folders/move',{'job_ids':['move-collision']})
        self.assertEqual(response.status_code,200,response.text)
        deadline=time.time()+10
        while app.MOVE_TASK['running'] and time.time()<deadline:time.sleep(.02)
        self.assertFalse(app.MOVE_TASK['running'])
        self.assertEqual(app.MOVE_TASK['moved'],1,app.MOVE_TASK)
        item=app.get('job','move-collision')
        target=Path(item['saved_path'])
        self.assertEqual(target.name,'move-collision - Música (2).wav')
        self.assertEqual(target.read_bytes(),self.wav)
        self.assertEqual(existing.read_bytes(),b'arquivo existente preservado')
        self.assertFalse(source.exists())
        self.assertEqual(self.client.get('/api/jobs/move-collision/file?play=true').content,self.wav)
        self.assertFalse(app.move_download('move-collision',folder))

    def test_15_locked_source_rolls_back_and_preserves_history(self):
        source=self.move_fixture('move-locked')
        folder=Path(TEMP.name)/'Destino bloqueado'
        folder.mkdir()
        original_unlink=Path.unlink
        def unlink(path,*args,**kwargs):
            if path==source:raise PermissionError('Arquivo em uso')
            return original_unlink(path,*args,**kwargs)
        with patch('pathlib.Path.unlink',unlink):
            with self.assertRaises(SourceError):app.move_download('move-locked',folder)
        self.assertEqual(source.read_bytes(),self.wav)
        self.assertEqual(app.get('job','move-locked')['saved_path'],str(source))
        self.assertFalse(list(folder.iterdir()))

    def test_16_cancel_move_preserves_original_and_removes_partial(self):
        source=self.move_fixture('move-cancel')
        folder=Path(TEMP.name)/'Destino cancelado'
        folder.mkdir()
        app.MOVE_CANCEL.set()
        try:
            with self.assertRaises(InterruptedError):app.move_download('move-cancel',folder)
        finally:app.MOVE_CANCEL.clear()
        self.assertEqual(source.read_bytes(),self.wav)
        self.assertFalse(list(folder.iterdir()))
        self.assertEqual(app.get('job','move-cancel')['saved_path'],str(source))

    def test_17_new_folder_only_applies_to_not_started_jobs(self):
        folder=Path(TEMP.name)/'Novos downloads';folder.mkdir()
        original=str(self.output)
        for identifier,status in [('pending-folder','Na fila'),('active-folder','Baixando')]:
            app.save('job',dict(id=identifier,title='Teste',artist='Acervo',output='mp3',state=status,output_folder=original,created=app.now()))
        app.config_write('output_folder',str(folder))
        response=self.post('folders/apply-pending',{})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(app.get('job','pending-folder')['output_folder'],str(folder))
        self.assertEqual(app.get('job','active-folder')['output_folder'],original)
        self.assertEqual(app.get('job','move-collision')['output_folder'],str(Path(TEMP.name)/'Pasta nova'))
        for identifier in ['pending-folder','active-folder']:app.patch_job(identifier,state='Cancelada',cancel=True)
        app.config_write('output_folder',original)

    def test_18_move_between_local_volumes(self):
        source=self.move_fixture('move-volume')
        workspace=Path.cwd()/'work'
        with tempfile.TemporaryDirectory(prefix='mover-unidade-',dir=workspace) as destination:
            folder=Path(destination).resolve()
            self.assertTrue(folder.is_relative_to(workspace.resolve()))
            if source.drive.lower()==folder.drive.lower():self.skipTest('O ambiente não tem duas unidades locais de teste.')
            self.assertTrue(app.move_download('move-volume',folder))
            saved=Path(app.get('job','move-volume')['saved_path'])
            self.assertEqual(saved.read_bytes(),self.wav)
            self.assertFalse(source.exists())

    def test_19_activity_search_can_load_retained_events_beyond_latest_100(self):
        rows=[]
        for i in range(150):
            event=dict(id=f'search-event-{i}',time=app.now(),action='teste_pesquisa',message=f'Registro {i}',job_id=None)
            rows.append(('event',event['id'],json.dumps(event),time.time()+i))
        with app.STATE:
            app.DB.executemany('INSERT OR REPLACE INTO records VALUES (?,?,?,?)',rows);app.DB.commit()
        latest=self.client.get('/api/state').json()['events']
        all_events=self.client.get('/api/state?events_all=true').json()['events']
        self.assertEqual(len(latest),100)
        self.assertNotIn('search-event-0',{e['id'] for e in latest})
        self.assertIn('search-event-0',{e['id'] for e in all_events})
        self.assertLessEqual(len(all_events),1000)

    def test_20_folder_preferences_validate_and_persist(self):
        for mode in ('ask','same'):
            response=self.post('settings',{'folder_mode':mode})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(app.config_read('folder_mode'),mode)
            self.assertEqual(self.client.get('/api/state').json()['folder_mode'],mode)
        self.assertEqual(self.post('settings',{'folder_mode':'C:/outra'}).status_code,422)

    def test_21_playlist_analysis_is_canonical_and_limited(self):
        media=dict(url='https://www.youtube.com/watch?v=abcdefghijk',title='Playlist teste',artist='Acervo',source='YouTube',formats=[])
        with patch('app.run_engine',return_value=[media]) as engine:
            response=self.post('analyze',{'url':'https://www.youtube.com/watch?v=abcdefghijk&list=PLteste_123'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(engine.call_args.args[0],dict(action='playlist',url='https://www.youtube.com/playlist?list=PLteste_123'))
            self.assertEqual(len(response.json()['results']),1)
        with patch('app.DESKTOP',False):
            self.assertEqual(self.post('analyze',{'url':'https://www.youtube.com/playlist?list=PLteste_123'}).status_code,400)

    def test_22_quick_batch_queues_all_and_deduplicates_active_files(self):
        ids=[app.remember(dict(url=f'https://example.com/playlist-{i}.wav',title=f'Faixa {i}',artist='Acervo',source='Arquivo direto',formats=[]))['id'] for i in range(120)]
        with patch('app.process_job'):
            body=dict(media_ids=ids+[ids[0]],output='mp3',authorized=True)
            self.assertEqual(self.post('jobs/quick',{**body,'authorized':False}).status_code,400)
            response=self.post('jobs/quick',body)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['count'],120)
            self.assertEqual(self.post('jobs/quick',body).json()['count'],0)
            self.assertEqual(sum(j['state']=='Na fila' for j in app.records('job')),120)
            self.post('jobs/cancel-all',{});app.JOBS.join()

    def test_23_quick_download_builds_real_mp3_and_mp4_with_or_without_cover(self):
        cover=Path(TEMP.name)/'conversion-cover.jpg'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=green:s=240x240','-frames:v','1','-threads','1',str(cover)],check=True)
        media=app.remember(dict(url='https://example.com/converter.wav',title='Conversão',artist='Acervo',source='Arquivo direto',duration=1,
                                formats=[dict(id='original',ext='wav',kind='audio',abr=None,size=len(self.wav))]))
        for output,image in [('mp3',cover),('mp4',cover),('mp4',None)]:
            with patch('app.download_direct',side_effect=lambda m,path,job:path.write_bytes(self.wav)),patch('app.cover_for',return_value=image):
                response=self.post('jobs/quick',dict(media_ids=[media['id']],output=output,authorized=True))
                self.assertEqual(response.status_code,200,response.text)
                item=next(j for j in app.records('job') if j.get('media_id')==media['id'] and j['state'] in app.ACTIVE)
                saved=self.wait(item['id'])
            self.assertEqual(saved['state'],'Concluída',saved)
            path=Path(saved['saved_path']);self.assertEqual(path.suffix,'.'+output)
            data=app.probe(path,video=output=='mp4')
            self.assertLessEqual(float(data['format']['duration']),2.1)
            self.assertEqual(data['format']['tags']['title'],'Conversão')
            self.assertEqual(saved['bitrate'],0)
            if output=='mp4':self.assertTrue(saved['video_from_audio'])
            if image:self.assertTrue(saved['cover_embedded'])

    def test_24_extracts_mp3_from_video_and_converts_other_codecs_to_mp4(self):
        fixture=Path(TEMP.name)/'other-codec.webm'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=160x90:d=1','-i',str(self.fixture),
                        '-c:v','libvpx-vp9','-c:a','libopus','-shortest',str(fixture)],check=True)
        media=app.remember(dict(url='https://www.youtube.com/watch?v=codecfixture',title='Outro codec',artist='Acervo',source='YouTube',duration=1,
                                formats=[dict(id='combined',ext='webm',kind='video',height=90,transcode=True,size=fixture.stat().st_size)]))
        def engine(request,job_id=None):
            import shutil
            shutil.copy2(fixture,Path(request['directory'])/'source.webm');return {'ext':'webm'}
        for output in ('mp3','mp4'):
            with patch('app.run_engine',side_effect=engine),patch('app.cover_for',return_value=None):
                self.assertEqual(self.post('jobs/quick',dict(media_ids=[media['id']],output=output,authorized=True)).status_code,200)
                item=next(j for j in app.records('job') if j.get('media_id')==media['id'] and j['state'] in app.ACTIVE)
                saved=self.wait(item['id'])
            self.assertEqual(saved['state'],'Concluída',saved)
            data=app.probe(saved['saved_path'],video=output=='mp4')
            if output=='mp4':self.assertTrue(any(s.get('codec_name')=='h264' for s in data['streams']))

    def test_25_quick_download_requires_review_and_never_marks_failed_conversion_ready(self):
        media=app.remember(dict(url='https://example.com/falha.wav',title='Falha',artist='Acervo',source='Arquivo direto',duration=1,
                                formats=[dict(id='original',ext='wav',kind='audio')]))
        app.save('track',dict(id='quick-review',title='Falha',artist='Acervo',state='Precisa revisar',media_id=None,candidates=[media]))
        body=dict(media_ids=[media['id']],item_id='quick-review',output='mp4',authorized=True)
        self.assertEqual(self.post('jobs/quick',body).status_code,400)
        self.assertEqual(self.post('library/quick-review/select',{'media_id':media['id']}).status_code,200)
        with patch('app.download_direct',side_effect=lambda m,path,job:path.write_bytes(self.wav)),patch('app.cover_for',return_value=None),patch('app.convert_media',side_effect=SourceError('Conversão recusada no teste')):
            response=self.post('jobs/quick',body);self.assertEqual(response.status_code,200,response.text)
            saved=self.wait(app.get('track','quick-review')['job_id'])
        self.assertEqual(saved['state'],'Erro')
        self.assertFalse(saved.get('saved_path'))
        self.assertEqual(self.client.get('/api/jobs/'+saved['id']+'/file').status_code,410)

    def test_26_skip_review_is_explicit_and_uses_candidate_without_claiming_confidence(self):
        candidate=app.remember(dict(url='https://example.com/sem-revisar.wav',title='Versão cover',artist='Outra banda',source='Internet Archive',duration=1,
                                   formats=[dict(id='original',ext='wav',kind='audio')],match=dict(confident=False,score=35,reason='Versão diferente.')))
        for identifier,skip,cached in [('review-default',False,False),('review-skipped',True,False),('review-cached',True,True)]:
            app.save('track',dict(id=identifier,title='Original',artist='Acervo',duration=1,state='Precisa revisar',media_id=None,candidates=[candidate] if cached else []))
            with patch('app.lookup',return_value=([candidate],[])) as lookup,patch('app.download_direct',side_effect=lambda m,path,job:path.write_bytes(self.wav)),patch('app.cover_for',return_value=None):
                response=self.post('library/download',dict(track_ids=[identifier],source='archive',output='mp3',authorized=True,skip_review=skip))
                self.assertEqual(response.status_code,200,response.text)
                saved=self.wait(response.json()['job_ids'][0])
            self.assertEqual(saved['state'],'Concluída' if skip else 'Precisa revisar',saved)
            if skip:
                self.assertTrue(saved['auto_selected'])
                app.probe(saved['saved_path'])
                self.assertFalse(app.get('track',identifier)['candidates'][0]['match']['confident'])
            else:self.assertFalse(saved.get('saved_path'))
            if cached:lookup.assert_not_called()
        self.assertTrue(any(e['action']=='escolha_sem_revisao' for e in app.records('event')))

    def test_27_skip_review_without_result_stays_not_found(self):
        app.save('track',dict(id='skip-empty',title='Sem fonte',artist='Acervo',state='Precisa revisar',candidates=[],media_id=None))
        with patch('app.lookup',return_value=([],[])),patch('app.download_direct') as download:
            response=self.post('library/download',dict(track_ids=['skip-empty'],source='archive',output='mp3',authorized=True,skip_review=True))
            saved=self.wait(response.json()['job_ids'][0])
        self.assertEqual(saved['state'],'Não encontrada')
        download.assert_not_called()
        self.assertFalse(saved.get('saved_path'))

    def test_28_all_unreviewed_tracks_queue_both_formats_without_duplicates(self):
        ids=[]
        for i in range(120):
            identifier=f'no-review-bulk-{i}'
            app.save('track',dict(id=identifier,title=f'Faixa {i}',artist='Acervo',state='Precisa revisar',candidates=[],media_id=None));ids.append(identifier)
        with patch('app.process_job'):
            for output in ('mp3','mp4'):
                response=self.post('library/download',dict(track_ids=ids,source='youtube',output=output,authorized=True,skip_review=True))
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['count'],120)
                self.assertTrue(all(app.get('job',identifier)['skip_review'] for identifier in response.json()['job_ids']))
            duplicate=self.post('library/download',dict(track_ids=ids,source='youtube',output='mp3',authorized=True,skip_review=True))
            self.assertEqual(duplicate.json()['count'],0)
            self.assertEqual(sum(j['state']=='Na fila' for j in app.records('job')),240)
            self.post('jobs/cancel-all',{});app.JOBS.join()

    def test_29_inline_progress_identifies_media_and_cancels_only_chosen_format(self):
        media=app.remember(dict(url='https://example.com/formatos.wav',title='Formatos',artist='Acervo',source='Arquivo direto',formats=[dict(id='original',ext='wav')]))
        app.save('track',dict(id='two-formats',title='Formatos',artist='Acervo',state='Encontrada',media_id=media['id'],candidates=[media]))
        with patch('app.process_job'):
            identifiers={}
            for output in ('mp3','mp4'):
                response=self.post('jobs/quick',dict(media_ids=[media['id']],item_id='two-formats',output=output,authorized=True))
                self.assertEqual(response.status_code,200,response.text)
                identifiers[output]=response.json()['job_ids'][0]
            snapshot=self.client.get('/api/state').json()['jobs']
            self.assertTrue(all(j['media_id']==media['id'] and not j['ready'] for j in snapshot if j['id'] in identifiers.values()))
            self.post('jobs/'+identifiers['mp3']+'/cancel',{})
            self.assertEqual(app.get('job',identifiers['mp3'])['state'],'Cancelada')
            self.assertEqual(app.get('job',identifiers['mp4'])['state'],'Na fila')
            self.post('jobs/cancel-all',{});app.JOBS.join()

    def test_30_retry_keeps_skip_review_choice(self):
        app.save('track',dict(id='retry-auto-choice',title='Repetir',artist='Acervo',state='Erro',media_id=None,candidates=[]))
        app.save('job',dict(id='retry-auto-job',item_id='retry-auto-choice',title='Repetir',artist='Acervo',output='mp3',state='Erro',automatic=True,skip_review=True,source_choice='archive',authorized=True,created=app.now()))
        with patch('app.process_job'):
            response=self.post('jobs/retry-auto-job/retry',{})
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(app.get('job',response.json()['job_ids'][0])['skip_review'])
            self.post('jobs/cancel-all',{});app.JOBS.join()

    def test_31_background_setting_persists_and_legacy_requests_preserve_it(self):
        self.assertTrue(self.client.get('/api/state').json()['background_enabled'])
        self.assertEqual(self.post('settings',dict(folder_mode='same',background_enabled=False)).status_code,200)
        self.assertFalse(self.client.get('/api/state').json()['background_enabled'])
        self.post('settings',dict(folder_mode='ask'))
        self.assertFalse(app.config_read('background_enabled'))
        self.post('settings',dict(folder_mode='same',background_enabled=True))
        self.assertTrue(self.client.get('/api/state').json()['background_enabled'])

    def test_32_language_defaults_to_english_and_preserves_legacy_requests(self):
        self.assertEqual(self.client.get('/api/state').json()['language'], 'en')
        response=self.client.get('/')
        self.assertEqual(response.headers['content-language'], 'en')
        self.assertIn('id="app-i18n"',response.text)
        self.assertEqual(self.post('settings',dict(folder_mode='same',language='pt-BR')).status_code,200)
        self.post('settings',dict(folder_mode='ask'))
        self.assertEqual(app.config_read('language'),'pt-BR')
        self.assertEqual(self.client.get('/').headers['content-language'],'pt-BR')
        self.assertEqual(self.post('settings',dict(folder_mode='same',language='xx')).status_code,422)
        self.assertEqual(app.config_read('language'),'pt-BR')
        self.post('settings',dict(folder_mode='same',language='en'))
        self.assertEqual(self.client.get('/api/state').json()['language'],'en')


if __name__=='__main__':unittest.main(verbosity=2)
