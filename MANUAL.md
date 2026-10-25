# Acervo para Windows 1.9.0

## Idiomas / Languages

English is the default interface language. To change it, open **Settings → Language**, select **English** or **Português (Brasil)**, then choose **Save settings**. The window reloads and the choice is kept when the app reopens.

O inglês é o idioma padrão. Para usar português, abra **Settings → Language**, escolha **Português (Brasil)** e clique em **Save settings**. A escolha permanece ao reabrir. Títulos, artistas, álbuns, arquivos e os estados internos das tarefas mantêm seus dados originais. Diálogos do Windows e páginas de serviços externos podem seguir o idioma próprio desses serviços.

1. Extraia o ZIP inteiro para uma pasta sua. Não abra o executável de dentro do ZIP.
2. Abra **Acervo.exe** com dois cliques. Mantenha a pasta `_internal` junto dele.
3. Clique em **Pastas → Mudar pasta dos downloads**, pesquise ou cole um link e clique em **MP3** ou **MP4**. Os botões entram diretamente na fila.

O programa já leva Python, yt-dlp, FFmpeg e Node. Você não precisa abrir terminal, hospedar nada ou importar JSON. Requer Windows 10/11 de 64 bits e Microsoft Edge WebView2 (presente no computador em que foi testado). Se a janela informar que esse componente está ausente, use o [instalador oficial da Microsoft](https://developer.microsoft.com/microsoft-edge/webview2/).

## Segundo plano e modo econômico

- O **X** e o botão de minimizar recolhem a janela para a bandeja do Windows, perto do relógio. Os downloads, conversões e a fila continuam no mesmo processo.
- Para voltar rapidamente, dê dois cliques no ícone da bandeja, escolha **Abrir Acervo** ou abra o mesmo **Acervo.exe** novamente. A instância já aberta é reutilizada, sem outra fila ou outro programa paralelo para a mesma pasta de dados.
- Para encerrar de verdade, use **Sair do Acervo** no menu da bandeja ou em **Configurações**. Isso pausa tarefas pendentes, fecha o leitor e libera o processo.
- Em **Configurações**, a opção **Continuar em segundo plano ao fechar ou minimizar** vem marcada. Desmarque e salve se quiser que o X encerre completamente. Há também **Recolher para a bandeja** e **Sair do Acervo**.
- Em segundo plano, a interface para de consultar a biblioteca e o Windows recebe uma meta de memória reduzida para o WebView2. Os responsáveis pelos downloads ficam bloqueados à espera de tarefas, sem consultar uma fila vazia continuamente. A limpeza periódica fica menos frequente e o leitor Spotify só é iniciado quando solicitado.
- Ao restaurar, a meta de memória volta ao normal e a interface se atualiza. O modo reduz atividades ociosas; o consumo não é zero e varia durante downloads/conversões, reprodução e uso de outras janelas.
- É necessário manter o PC ligado, sem suspensão e com internet para baixar. Esta versão não instala serviço nem configura início automático com o Windows.
- O suporte usa [NotifyIcon](https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.notifyicon) e a [meta de memória do WebView2](https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2.memoryusagetargetlevel). A suspensão completa foi recusada pelo WebView2 deste PC; o modo entregue usa memória reduzida e consultas pausadas, sem prometer suspensão completa.

## ZIP portátil e atualização

- **Acervo-Windows-1.9.0.zip** contém a pasta Acervo, o executável, `_internal` e instruções. Python, yt-dlp, FFmpeg e Node estão incluídos. Mantenha todos esses componentes juntos.
- O ZIP não contém seu banco local, músicas ou perfil de conexão. Uma extração nova começa com biblioteca vazia.
- Para atualizar uma cópia existente, use **Sair do Acervo**, extraia os arquivos do programa sobre a pasta antiga e preserve `Dados` e suas músicas. Não substitua apenas o EXE nem apague a pasta `_internal`.

## Spotify sem Premium

Esta versão lê a página de Músicas Curtidas que sua conta já consegue abrir. **Não utiliza a API de desenvolvedor do Spotify**, portanto não pede Client ID, Client Secret ou assinatura Premium.

1. Em **Minhas curtidas**, clique em **Abrir Spotify**.
2. O Acervo abre uma janela normal do Edge ou Chrome, usando um perfil temporário separado do seu navegador pessoal.
3. Faça você mesmo o login no site oficial do Spotify e abra **Músicas Curtidas**.
4. Volte ao Acervo e clique em **Ler curtidas**. Ele percorre a página, lendo título, artista, álbum e duração quando disponíveis.
5. Marque **Selecionar todas** ou escolha músicas, defina a fonte da pesquisa e clique em **MP3** ou **MP4**. A busca e os downloads continuam pela fila. Candidatos duvidosos aparecem em **Revisar músicas**; as outras tarefas continuam normalmente.

Não é preciso reproduzir as músicas para importar. O Acervo não lê sua senha, não extrai cookies/tokens nem solicita o áudio do Spotify. Ele também não resolve verificações de acesso. A entrada e eventuais verificações devem ser feitas por você no navegador.

**A leitura depende do layout e da disponibilidade do Spotify Web.** Pode falhar ou importar só parte da lista. O programa mostra a quantidade lida e avisa quando a leitura pode estar incompleta. O limite é de 5.000 músicas por leitura.

O perfil de conexão é temporário: ao desconectar ou sair completamente do Acervo, o programa fecha sua janela de conexão e solicita a limpeza desse perfil. Pode ser necessário entrar novamente na próxima vez. Sua lista já importada fica salva no Acervo. Não copie a pasta Dados enquanto uma conexão estiver aberta para compartilhar com outras pessoas.

## Progresso no botão de formato

- Em Descobrir, Minhas curtidas e nas opções de revisão, MP3 e MP4 têm estados separados. Ao entrar na fila, o botão vira um círculo com X. O X cancela somente aquela tarefa/formato.
- O círculo usa o progresso informado pela tarefa. Se a fonte não informar o total, mostra movimento sem inventar porcentagem. Na fila ele aguarda; durante conversão/validação ainda não é considerado concluído.
- Quando o arquivo estiver validado, salvo e presente na pasta, aparece um ícone de verificado. Clique nele para abrir o áudio ou vídeo no player. O outro formato permanece ao lado para baixar.
- Arquivo removido ou movido fora do Acervo perde o verificado após atualizar a lista. O Histórico continua registrando o ocorrido. Os controles são reconstruídos pelos registros ao reabrir.

## Baixar sem revisar

- O comportamento normal continua exigindo revisão quando a gravação for duvidosa.
- Em Minhas curtidas, escolha o formato no painel **Baixar sem revisar**. Cada música tem um botão **Baixar sem revisar (MP3/MP4)**; a aba Revisar músicas também permite escolher esse formato.
- **Selecionadas sem revisar** envia a seleção. **Baixar tudo sem revisar** envia a biblioteca inteira, inclusive músicas fora da pesquisa e das páginas visíveis. Arquivos já salvos no mesmo formato e tarefas ativas desse formato são ignorados.
- Esse modo utiliza o candidato mais bem classificado, mesmo sem confiança na correspondência. Pode baixar cover, remix, versão ao vivo ou outra gravação. Não altera a avaliação para fingir que a correspondência foi confirmada.
- Sem candidato, a tarefa fica **Não encontrada**. Falhas da fonte continuam como **Erro**. Os limites, a pasta escolhida, cancelamento e verificações dos arquivos continuam valendo.
- A escolha automática aparece nos logs com horário e na tarefa quando usada. Não habilita um modo permanente: os próximos botões MP3/MP4 normais conservam a revisão.

## Downloads e player

- Fontes: arquivos HTTPS públicos de áudio, itens públicos do Internet Archive e vídeos e playlists públicas do YouTube via [yt-dlp](https://github.com/yt-dlp/yt-dlp).
- Baixe apenas conteúdo próprio ou autorizado. Nenhum DRM ou controle de acesso é contornado.
- O áudio é salvo diretamente na pasta escolhida como **Artista - Música**. Se já existir, é acrescentado um número; o arquivo existente é preservado.
- Toda a seleção pode entrar em uma operação: até 5.000 tarefas pendentes na versão Windows, com duas transferências simultâneas. A fila continua automaticamente. Não é preciso voltar a cada quatro ou vinte músicas. Limites: 100 MB e 20 minutos por arquivo.
- **MP3** prefere baixar só áudio. Se existir apenas vídeo com áudio, extrai o áudio desse arquivo. A conversão direta usa VBR de qualidade 0 (melhor ajuste de conversão do [yt-dlp](https://github.com/yt-dlp/yt-dlp#post-processing-options)), sem melhorar a gravação original. **MP4** escolhe o melhor vídeo disponível dentro de 1080p/100 MB; combina os fluxos e converte codecs quando necessário. Se a fonte só oferecer áudio, cria um vídeo com capa; sem imagem pública, usa fundo neutro. A conversão não cria um videoclipe.
- Os botões **Pasta** ao lado de MP3/MP4 abrem a seleção de pasta do Windows. Em **Configurações**, escolha se quer usar sempre a mesma pasta ou perguntar a cada download/lote. Cada tarefa mantém o destino definido ao entrar na fila; cancelar a escolha de pasta não inicia novas tarefas.
- Itens já ativos ou salvos no mesmo formato não são duplicados pelo envio de toda a seleção. A opção **Cancelar fila inteira** interrompe a fila; arquivos já concluídos são preservados. **Pesquisar selecionadas** continua disponível para somente pesquisar.
- A lista mostra quantas selecionadas estão prontas, ainda sem busca ou aguardando revisão. Importar as curtidas não significa baixar os áudios.
- Para áudio original e escolhas manuais, use **Mais formatos** nas curtidas ou o ícone **Outros formatos** no resultado. Formato original preserva o áudio obtido. A conversão para MP3 não melhora a gravação e respeita as opções de taxa da fonte.
- Só aparece **Concluída** depois de validar e terminar de salvar o arquivo no PC.
- Use **Ouvir**, **Ver vídeo** e **Abrir pasta** no Histórico. O MP4 abre no player de vídeo da janela. **Abrir arquivos locais** permite ouvir áudios já salvos. A reprodução depende do suporte do componente de navegador ao codec.
- Os arquivos concluídos são seus arquivos locais: não expiram nem são removidos pela limpeza de temporários do programa.
- Pesquisa, leitura do Spotify e obtenção exigem internet; ouvir arquivos já salvos pode ser feito offline.

## Pesquisar nas abas

- **Minhas curtidas**, **Revisar músicas**, **Fila de downloads**, **Histórico**, **Atividade** e **Configurações** têm campo, botão **Pesquisar** e **Limpar**. **Descobrir** conserva a busca de músicas nas fontes externas.
- A pesquisa da aba filtra seus registros, considera todas as páginas e aceita trechos de título, artista, álbum, estado e formato quando esses dados existem. Em Atividade, procura mensagem, data e tarefa nos até 1.000 registros recentes mantidos pelo programa.
- Maiúsculas e acentos são ignorados. Os filtros de cada aba são independentes e ficam lembrados enquanto a janela está aberta. Também é possível digitar e aguardar o filtro ou pressionar Enter.
- Com filtro nas curtidas, **Selecionar resultados** inclui os resultados de todas as páginas e substitui a seleção anterior. O resumo informa seleções que estejam fora da pesquisa. Sem filtro, **Selecionar todas** continua incluindo a biblioteca inteira.
- No Histórico, **Mover resultados baixados** usa apenas os arquivos encontrados. O menu **Pastas** mantém a movimentação da biblioteca inteira. **Cancelar fila inteira** cancela todas as pendentes, inclusive as que estiverem fora da pesquisa.

## Playlists: suporte atual

- YouTube: cole o link de uma playlist pública e clique em **Analisar link**. Mostra até 500 vídeos por análise, com 50 por página. Marque **Todas** ou selecione vídeos e use **MP3** ou **MP4** para enviar o lote em uma operação. Os formatos de cada vídeo são verificados quando ele chega à sua vez. Um link de vídeo com `list=` analisa a playlist; para somente aquele vídeo, remova `list=` do link. Itens privados ou indisponíveis podem ficar de fora ou terminar com erro. Playlists maiores que 500 vídeos são recusadas para não importar uma lista truncada como completa.
- Spotify: leitura de **Músicas Curtidas** e procura das gravações nas outras fontes. Playlists genéricas do Spotify por link ainda não são importadas. O programa não extrai áudio do Spotify.

## Revisar músicas

- A aba reúne gravações com candidatos duvidosos e itens com erro ou não encontrados. Faixas que ainda não foram pesquisadas ficam em Minhas curtidas.
- Use **Comparar** para ver título, artista, álbum, duração e os motivos da dúvida. **Buscar novamente** repete a pesquisa na fonte escolhida em Minhas curtidas.
- **Confirmar gravação** apenas confirma a escolha. No candidato correto, **MP3** ou **MP4** confirma e envia para a fila; nenhuma correspondência duvidosa é aceita automaticamente.

## Configurações de pasta

- **Sempre baixar na mesma pasta**: os botões diretos usam o destino salvo, sem abrir uma nova escolha.
- **Sempre perguntar onde baixar**: abre o seletor do Windows uma vez para cada download ou lote. Cancelar não adiciona novas tarefas nem confirma uma gravação.
- Clique em **Salvar configurações**. A preferência fica no banco local e permanece ao reabrir. Os downloads já na fila conservam a pasta original.
- **Trocar pasta** define os próximos destinos. Para mover arquivos existentes, continue usando **Histórico → Mover** ou **Pastas → Mover arquivos baixados**.

## Mover arquivos e mudar de pasta

- O botão **Pastas**, no topo da janela, abre as opções de destino e movimentação.
- **Mudar pasta dos downloads** escolhe onde salvar os próximos arquivos. A opção marcada aplica a pasta também às músicas que ainda não começaram; as transferências em andamento terminam no destino atual.
- Para um arquivo concluído, use **Histórico → Mover**. Para todos os arquivos concluídos disponíveis, use **Pastas → Mover arquivos baixados** ou o botão equivalente no Histórico.
- Escolha a nova pasta, confira a quantidade e confirme em **Mover para esta pasta**. Escolher a pasta por si só não inicia a movimentação. Mover arquivos existentes não troca o destino dos próximos downloads.
- Funciona entre pastas e unidades locais: cada arquivo é copiado para um temporário na unidade de destino, sincronizado e verificado antes de remover a origem. Arquivos com o mesmo nome são preservados e recebem um número. Histórico, reprodução e Abrir pasta passam a usar o novo endereço.
- O progresso e **Parar movimentação** ficam em Pastas e no Histórico. Sair do programa interrompe a movimentação; arquivos ainda não processados ficam na origem. Os já movidos permanecem na pasta nova.
- O player do Acervo é pausado ao confirmar para liberar os arquivos. Se outro programa bloquear o arquivo, a falha é registrada e o original é preservado.

## Capas e listas leves

- Curtidas, fila e histórico exibem capas quando a fonte oferece uma imagem pública. Para itens Spotify já importados, a capa pode ser consultada pelo oEmbed público, sem novo login, Premium ou credenciais de desenvolvedor. Quando necessário, é usada a miniatura da gravação escolhida na fonte. Uma miniatura do YouTube pode ser diferente da capa do álbum.
- Imagens são carregadas conforme aparecem na tela, convertidas em JPEG pequeno e guardadas em cache local de até 128 MB. Fontes indisponíveis usam o ícone padrão. Nenhuma capa é inventada.
- Novos arquivos MP3, M4A, MP4 e FLAC recebem a capa quando disponível. A inclusão de metadados não recodifica o áudio. Outros formatos continuam com capa nas listas, quando houver imagem.
- Para arquivos antigos, abra **Histórico → Atualizar capas dos arquivos**. O progresso aparece nessa tela e nos logs. Cada arquivo é copiado, recebe a capa e é validado antes de substituir o original; se algo falhar, o original é preservado. Capas já existentes são mantidas.
- As listas mostram **50 itens por página** e só a lista da tela aberta é atualizada. Sem pesquisa ativa, **Selecionar todas continua selecionando a biblioteca inteira**, inclusive as outras páginas. A paginação não reduz a quantidade enviada à fila.

## Logs e dados

- Aba **Atividade**: eventos com data e hora do seu computador.
- Botão **Abrir pasta de logs**: abre os registros locais.
- Pasta `Dados`: biblioteca, histórico, logs, cache da janela e perfil temporário da conexão, junto ao programa.
- Pasta `Músicas`: destino inicial; você pode trocar pelo botão na janela.

O programa usa comunicação interna apenas em `127.0.0.1`, com porta aleatória e acesso temporário. Isso é iniciado e encerrado automaticamente pelo executável. Não instala serviço do Windows nem disponibiliza um servidor na internet.

Com a opção de segundo plano marcada, o X recolhe a janela e a fila continua. **Sair do Acervo** encerra completamente e pausa a fila; na próxima abertura as pendentes continuam. Transferências interrompidas reiniciam o arquivo em vez de prometer retomada por bytes. Com o programa encerrado, PC desligado ou em suspensão, não há downloads. Mover a pasta inteira preserva os dados locais; mover arquivos separadamente pode fazer o Histórico deixar de encontrá-los.

## Verificação e dependências externas

Consulte `TESTES.md` e `DIARIO.md` para saber exatamente o que foi testado. O sucesso com um link de teste não garante acesso a todas as músicas. YouTube e Spotify podem mudar o site ou recusar acesso; nesse caso, o programa deve mostrar a falha.

O código-fonte é entregue separadamente. Os componentes de terceiros mantêm suas próprias licenças. Este é um pacote pessoal; não contém assinatura digital ou publicação em loja.
