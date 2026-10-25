# Código-fonte e componentes da versão 1.9.0

O pacote **Acervo-Fontes-1.9.0.zip**, disponível junto do programa em [Releases](https://github.com/yonosan/Acervo/releases), reúne o código do Acervo, recursos, instruções de compilação, textos de licença e fontes de terceiros. Não é necessário baixá-lo para usar o programa.

- Código do Acervo: GNU GPL versão 3; texto completo em LICENSE. A publicação sob esta licença foi autorizada pelo responsável pelo projeto.
- Fontes de 39 distribuições Python nas versões registradas no ambiente de compilação: arquivos de origem do PyPI, conferidos pelos SHA-256 publicados pelo índice. Incluem Mutagen 1.48.1, sob GPL-2.0-or-later.
- Playwright e playwright-python 1.64.1: arquivos das tags oficiais correspondentes.
- FFmpeg: código da revisão `5d4d3bdc61412641883a45e060e810f80ea7f4b5` e receitas de compilação da revisão `f523eabde4d012ad85cd75e9a3cd6bc619f868c6` de yt-dlp/FFmpeg-Builds. As receitas contêm os endereços, revisões e patches de suas dependências. Essas dependências externas e o ambiente de compilação são obtidos conforme as receitas; não se afirma que o pacote foi recompilado nesta preparação.
- FFmpeg/ffprobe distribuídos: `N-125875-g5d4d3bdc61-20260731`, build Windows x64 com GPL versão 3 ou posterior. A configuração original está em FFMPEG-BUILD.txt, dentro dos pacotes.
- Origem dos binários FFmpeg: [autobuild-2026-07-31-16-16](https://github.com/yt-dlp/FFmpeg-Builds/releases/tag/autobuild-2026-07-31-16-16). Não foram modificados pelo Acervo.
- Python 3.11 e Node.js 26.7.0 são runtimes usados sem modificações. Licenças e avisos acompanham o ZIP do programa; a origem do Node está em https://nodejs.org/dist/v26.7.0/node-v26.7.0.tar.gz.

ORIGENS-DAS-DEPENDENCIAS.json registra URLs, versões, nomes e hashes das fontes. Preserve a disponibilidade do código correspondente ao redistribuir executáveis. As fontes das dependências mantêm suas licenças originais.
