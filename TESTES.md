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
