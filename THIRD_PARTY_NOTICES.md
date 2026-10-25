# Dependências e licenças

O repositório contém o código do Acervo e suas referências a dependências. O ZIP Windows em Releases inclui Python, bibliotecas, Node e FFmpeg. Os textos completos de licença acompanham o programa na pasta LICENSES; o pacote de fontes inclui as origens registradas em ORIGENS-DAS-DEPENDENCIAS.json e as fontes descritas em SOURCES.md.

| Componente | Versão registrada | Licença declarada | Origem |
| --- | --- | --- | --- |
| FastAPI | 0.162.0 | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | 0.54.0 | BSD-3-Clause | https://github.com/Kludex/uvicorn |
| yt-dlp (pacote Python) | 2026.10.21 | Unlicense | https://github.com/yt-dlp/yt-dlp |
| pywebview | 6.2.1 | BSD-3-Clause | https://github.com/r0x0r/pywebview |
| Playwright | 1.64.1 | Apache-2.0 | https://github.com/microsoft/playwright-python |
| Mutagen | 1.48.1 | GPL-2.0-or-later | https://github.com/quodlibet/mutagen |

Metadados conferidos nas instalações locais usadas na preparação. As licenças completas e avisos pertencem aos respectivos projetos.

O código do Acervo foi licenciado sob GNU GPL versão 3 com autorização do responsável. Mutagen permanece sob GPL-2.0-or-later. Os demais componentes conservam seus termos e avisos; não são todos MIT ou Unlicense.

FFmpeg e ffprobe deste pacote: N-125875-g5d4d3bdc61-20260731, Windows x64, GNU GPL versão 3 ou posterior. O build habilita GPL e version3, sem enable-nonfree. Configuração original em FFMPEG-BUILD.txt; fonte e receitas de compilação identificadas em SOURCES.md. Consulte também https://ffmpeg.org/legal.html. Os binários não foram modificados.

Os executáveis oficiais do yt-dlp podem conter componentes sob GPL e outras licenças, diferentes da licença isolada do pacote Python. Consulte https://github.com/yt-dlp/yt-dlp#licensing.

Ferramentas de desenvolvimento, runtimes e dependências transitivas também conservam suas próprias licenças. Nenhum aviso deste arquivo substitui suas exigências.
