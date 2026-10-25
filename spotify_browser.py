"""Lê metadados renderizados no Spotify Web; login feito pelo usuário."""
from pathlib import Path
import queue
import re
import shutil
import tempfile
import threading
import time
from urllib.parse import urlsplit

from security import SourceError

READ_ROWS = r"""() => {
 const output=[];
 const rows=[...document.querySelectorAll('main [role="row"], [data-testid="tracklist-row"]')];
 for(const row of new Set(rows)) {
   if(!row.getBoundingClientRect().height) continue;
   const links=[...row.querySelectorAll('a[href]')];
   const track=links.find(a=>/\/track\/[A-Za-z0-9]+/.test(a.getAttribute('href')));
   if(!track) continue;
   const id=track.getAttribute('href').match(/\/track\/([A-Za-z0-9]+)/)?.[1];
   const title=(track.innerText || track.getAttribute('aria-label') || '').trim();
   const artists=[...new Set(links.filter(a=>/\/artist\//.test(a.getAttribute('href'))).map(a=>a.innerText.trim()).filter(Boolean))];
   const album=links.find(a=>/\/album\//.test(a.getAttribute('href')))?.innerText.trim() || '';
   const times=[...row.querySelectorAll('[role="gridcell"], [aria-colindex]')].map(c=>c.innerText.trim()).filter(t=>/^\d{1,3}:\d{2}(?::\d{2})?$/.test(t));
   const raw=times.at(-1); const duration=raw ? raw.split(':').reduce((a,b)=>a*60+Number(b),0) : null;
   const image=row.querySelector('img')?.currentSrc || row.querySelector('img')?.getAttribute('src');
   if(id && title && artists.length) output.push({id:'spotify-'+id,spotify_id:id,title,artist:artists.join(', '),album,duration,thumbnail:image?.startsWith('https://') ? image : null});
 }
 const grid=document.querySelector('main [role="grid"], [data-testid="playlist-tracklist"]');
 return {rows:output,total:Number(grid?.getAttribute('aria-rowcount')) || null};
}"""

SCROLL_LIST = r"""(reset) => {
 const grid=document.querySelector('main [role="grid"], [data-testid="playlist-tracklist"]');
 if(!grid) return {found:false,bottom:false,moved:false};
 let scroller=grid;
 while(scroller && !(scroller.clientHeight>0 && scroller.scrollHeight>scroller.clientHeight+10 && /auto|scroll/.test(getComputedStyle(scroller).overflowY))) scroller=scroller.parentElement;
 if(!scroller) return {found:true,bottom:true,moved:false};
 const before=scroller.scrollTop;
 scroller.scrollTop=reset ? 0 : before+Math.max(300,scroller.clientHeight*.75);
 return {found:true,moved:Math.abs(scroller.scrollTop-before)>1,bottom:scroller.scrollTop+scroller.clientHeight>=scroller.scrollHeight-4};
}"""


def is_liked_page(url):
    location=urlsplit(url)
    return location.scheme=='https' and location.hostname=='open.spotify.com' and location.path.rstrip('/').endswith('/collection/tracks')


def current_spotify_page(context, navigate=False):
    pages=[p for p in context.pages if not p.is_closed()]
    for candidate in reversed(pages):
        if is_liked_page(candidate.url):
            return candidate
    spotify=[p for p in pages if urlsplit(p.url).hostname=='open.spotify.com']
    if navigate and spotify:
        page=spotify[-1]
        page.goto('https://open.spotify.com/collection/tracks',wait_until='domcontentloaded',timeout=30000)
        return page
    return spotify[-1] if spotify else pages[-1] if pages else None


def collect_tracks(page, save_track, stopped, progress):
    if not is_liked_page(page.url):
        raise SourceError('No navegador do Spotify, entre na sua conta e abra Músicas Curtidas. Depois clique em Ler curtidas no Acervo.')
    for attempt in range(30):
        if stopped.is_set():
            raise SourceError('Leitura interrompida antes de carregar a lista.')
        initial=page.evaluate(READ_ROWS)
        if initial.get('rows'):
            break
        progress(0,initial.get('total'))
        page.wait_for_timeout(500)
    else:
        raise SourceError('A página de Músicas Curtidas está aberta, mas o leitor não conseguiu reconhecer as linhas. Aguarde o carregamento e tente novamente. Se a lista já estiver visível, o layout do Spotify precisa de ajuste no leitor.')
    page.evaluate(SCROLL_LIST,True)
    page.wait_for_timeout(800)
    found=set()
    unchanged=0
    bottom=False
    total=None
    deadline=time.monotonic()+600
    while time.monotonic()<deadline and len(found)<5000:
        if stopped.is_set():
            raise SourceError(f'Leitura interrompida. {len(found)} música(s) já foram importadas.')
        data=page.evaluate(READ_ROWS)
        total=data.get('total') or total
        previous=len(found)
        for track in data.get('rows',[]):
            if not re.fullmatch(r'spotify-[A-Za-z0-9]{10,40}',str(track.get('id',''))):
                continue
            if track['id'] not in found and track.get('title') and track.get('artist'):
                track.update(title=track['title'][:300],artist=track['artist'][:300],album=track.get('album','')[:300],
                             state='Precisa revisar',candidates=[],media_id=None)
                save_track(track)
                found.add(track['id'])
        if not found:
            raise SourceError('Não encontrei a lista de músicas na página. Termine o login no navegador, abra Músicas Curtidas e tente Ler curtidas novamente. Não há leitura de senha ou resolução automática de verificações.')
        progress(len(found),total)
        unchanged=unchanged+1 if len(found)==previous else 0
        movement=page.evaluate(SCROLL_LIST,False)
        bottom=movement.get('bottom',False)
        if unchanged>=3 and bottom:
            break
        if unchanged>=7:
            raise SourceError(f'A página deixou de carregar novas linhas. {len(found)} música(s) foram importadas; a lista pode estar incompleta. Tente novamente ou confira o layout do Spotify.')
        page.wait_for_timeout(650)
    complete=bottom and unchanged>=3 and (not total or len(found)>=total-1)
    return {'count':len(found),'complete':complete,'total':total}


class SpotifyBrowser:
    def __init__(self,directory,save_track,event):
        self.directory=Path(directory)
        self.save_track,self.event=save_track,event
        self.opened=False
        self.connected=False
        self.busy=False
        self.message='Conta gratuita aceita. Abra o Spotify, faça o login no navegador e clique em Ler curtidas.'
        self.commands=queue.Queue(maxsize=4)
        self.stopping=threading.Event()
        self.cancel=threading.Event()
        self.thread=threading.Thread(target=self._run,daemon=True)
        self.thread.start()

    def status(self):
        return {'configured':True,'connected':self.connected,'browser_open':self.opened,'busy':self.busy,
                'message':self.message,'method':'browser','requires_premium':False}

    def begin(self):
        if self.busy:
            raise SourceError('Aguarde a leitura atual ou use Parar leitura.')
        self.commands.put_nowait('open')
        self.message='Abrindo uma janela própria do navegador para o Spotify. Você faz o login normalmente no site oficial.'
        self.event('spotify_abrir','Solicitada abertura do Spotify no navegador. Conta gratuita aceita; sem API de desenvolvedor.')

    def start_sync(self):
        if not self.opened:
            raise SourceError('Clique em Abrir Spotify e faça o login no navegador primeiro.')
        if self.busy:
            raise SourceError('A leitura da lista já está em andamento.')
        self.busy=True
        self.cancel.clear()
        self.commands.put_nowait('read')

    def stop_read(self):
        self.cancel.set()
        self.message='Interrompendo a leitura; as músicas já lidas serão preservadas.'

    def disconnect(self):
        self.cancel.set()
        self.commands.put_nowait('close')

    def close(self):
        self.cancel.set()
        self.stopping.set()
        try:self.commands.put_nowait('stop')
        except queue.Full:pass
        self.thread.join(timeout=35)

    def _run(self):
        context=None
        profile=None
        page=None
        try:
            first_command=self.commands.get()
            if self.stopping.is_set():return
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                while not self.stopping.is_set():
                    try:
                        if first_command is not None:
                            command,first_command=first_command,None
                        else:
                            command=self.commands.get(timeout=.2 if context else None)
                    except queue.Empty:
                        if context:
                            try:
                                page=current_spotify_page(context)
                                if page:
                                    page.wait_for_timeout(50)
                                else:
                                    self.opened=False
                            except Exception:
                                self.opened=False
                        continue
                    try:
                        if command=='open':
                            if context:
                                page=current_spotify_page(context)
                                if page:page.bring_to_front()
                                else:
                                    context.close()
                                    context=None
                                    self.opened=False
                            if not context:
                                self.directory.mkdir(parents=True,exist_ok=True)
                                profile=Path(tempfile.mkdtemp(prefix='sessao-',dir=self.directory))
                                for channel in ('msedge','chrome'):
                                    try:
                                        context=playwright.chromium.launch_persistent_context(str(profile),channel=channel,headless=False,
                                            viewport=None,accept_downloads=False,args=['--no-default-browser-check','--no-first-run'])
                                        break
                                    except Exception:
                                        if channel=='chrome':
                                            raise SourceError('Não consegui abrir o Edge nem o Chrome. Instale um desses navegadores e tente novamente.')
                                page=context.pages[0] if context.pages else context.new_page()
                                self.opened=True
                                page.goto('https://open.spotify.com/collection/tracks',wait_until='domcontentloaded',timeout=30000)
                            self.message='No navegador: faça login e abra Músicas Curtidas. Depois volte ao Acervo e clique em Ler curtidas. Não clique em reproduzir para importar.'
                        elif command=='read':
                            if not context:
                                raise SourceError('A janela do Spotify foi fechada. Clique em Abrir Spotify novamente.')
                            page=current_spotify_page(context,navigate=True)
                            if page is None:
                                raise SourceError('Nenhuma aba do Spotify está aberta nessa janela. Clique em Abrir Spotify novamente.')
                            self.event('spotify_leitura_inicio',f'Iniciada leitura na aba de curtidas. {len([p for p in context.pages if not p.is_closed()])} aba(s) na janela de conexão.')
                            def progress(count,total):
                                self.message=f'Lendo a página do Spotify: {count} música(s) importadas'+(f' de aproximadamente {total}.' if total else '…')
                            result=collect_tracks(page,self.save_track,self.cancel,progress)
                            self.connected=True
                            self.message=f'{result["count"]} curtidas lidas da página. '+('Fim da lista alcançado.' if result['complete'] else 'A lista pode estar incompleta; confira no Spotify e tente novamente se necessário.')
                            self.event('spotify_lido',self.message)
                        elif command=='close':
                            if context:context.close()
                            context=None
                            self.opened=False
                            self.connected=False
                            self.message='Janela de conexão fechada. A lista importada permanece no Acervo.'
                            if profile:
                                shutil.rmtree(profile,ignore_errors=True)
                                profile=None
                            self.event('spotify_desconectado','Janela própria do Spotify fechada e limpeza do perfil temporário solicitada.')
                    except Exception as exc:
                        self.message=str(exc) if isinstance(exc,SourceError) else 'Não consegui ler a página do Spotify. Confira se a janela está aberta, o login foi concluído e Músicas Curtidas está visível. Mudanças no site podem exigir ajuste no leitor.'
                        self.event('spotify_erro',self.message)
                    finally:
                        self.busy=False
        except Exception:
            self.message='Não consegui iniciar o leitor do navegador. Extraia o pacote completo do Acervo.'
            self.event('spotify_erro',self.message)
        finally:
            if context:
                try:context.close()
                except Exception:pass
            if profile:shutil.rmtree(profile,ignore_errors=True)
            self.opened=False
            self.busy=False
