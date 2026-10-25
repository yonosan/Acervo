# Verificação do Acervo

## 25/10/2026, 09:45 — Versão 1.9.0: inglês por padrão

- 57 testes passaram em 19,932 s: 52 fluxos existentes, preferência de idioma e quatro testes do catálogo/traduções. As variáveis das mensagens e nomes dentro delas foram conferidos. A conferência do catálogo foi repetida após completar os textos.
- Edge com perfil e biblioteca fictícios: inglês inicial, português salvo e preservado após recarregar, retorno ao inglês, mensagens de erro, resumos, revisão e escolha de formatos. Os títulos e artistas de teste iguais a palavras da interface não foram traduzidos. Nenhum erro JavaScript e nenhuma sobra conhecida de português nas sete abas verificadas.
- Janela real do Windows: título e menus Open Acervo/Exit Acervo em inglês; mudança para português e reabertura em um segundo processo preservando a escolha. Volta ao inglês também verificada. Nenhuma conta real ou download pessoal foi usado.
- Os dados persistidos das músicas e os valores internos dos estados das tarefas continuam compatíveis com versões anteriores. A tradução é aplicada na interface e em seus controles; nomes de arquivos e metadados são preservados.
- Sem teste em outro computador, novo login real nas fontes ou assinatura digital. Os serviços externos e diálogos do sistema têm seus próprios idiomas.

# Verificação da cópia para publicação

## 24/10/2026, 23:46 — Windows local

- 52 testes passaram em 24,749 segundos nesta cópia, usando o ambiente Python já instalado para o projeto, FFmpeg real, arquivos sintéticos e dados temporários.
- A suíte cobre fila, cancelamento, retomada após reinício, conversões MP3/MP4, metadados/capas, movimentação sem sobrescrever, revisão de correspondências, importação de metadados simulados e preferências de segundo plano.
- Os 16 arquivos originais de código, recursos e dependências foram conferidos por SHA-256 contra a versão 1.8.0. Nenhuma alteração funcional foi feita para a publicação.
- A primeira tentativa restrita ficou bloqueada ao criar a conexão interna do asyncio no Windows. Foi interrompida e a suíte completa passou em uma execução com acesso à comunicação local. O aplicativo instalado não foi encerrado ou atualizado.
- O pacote de publicação é formado por uma lista explícita de arquivos. Não inclui banco, músicas, histórico pessoal, logs de execução, perfis de navegador, caches, executável ou ambiente Python.
- A varredura de caminhos locais e padrões conhecidos de credenciais não encontrou dados pessoais nos arquivos selecionados. A senha de fixture e o caminho C:/outra nos testes são valores fictícios.

## Limites da verificação

- Não houve novo teste com uma conta real do Spotify ou downloads de músicas comerciais nesta preparação.
- Não foi feita instalação em outro computador ou em um Windows limpo. O ambiente de testes já tinha as dependências necessárias.
- Os testes não equivalem a uma análise jurídica, auditoria completa de licenças ou garantia de funcionamento futuro das fontes externas.
