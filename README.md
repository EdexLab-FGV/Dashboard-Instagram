# Painel Instagram EdexLab

Dois painéis que se atualizam sozinhos, pensados para o time comercial:

- **`index.html`**: conta do Instagram
- **`anuncios.html`**: campanhas de anúncios (Meta Ads)
- **`cursos.html`**: campanhas por curso, cidade e tipo de criativo, com histórico semanal desde jan/2024
- **`comercial.html`**: do lead à matrícula (RD Station CRM): custo por matrícula, funil, leads esfriando, fontes e motivos de perda

Os dois usam a mesma senha e têm um botão no topo para alternar entre eles.

- **GitHub Actions** coleta os dados da Instagram Graph API a cada 6 horas.
- Os dados ficam em `data/`, **criptografados** com a senha do painel.
- **GitHub Pages** publica o painel. Quem abre o link precisa digitar a senha.

## O que o painel mostra

| Seção | O que responde |
|---|---|
| Resumo | O que aconteceu no período, em frases |
| Termômetro de interesse | Visitas ao perfil e cliques no link da bio por dia |
| Posts que geram procura | Quais posts levaram gente ao perfil e geraram seguidores |
| Para o comercial | O melhor post de cada tema, com mensagem pronta para o WhatsApp |
| Pessoas | Comentários com cara de lead (valor, inscrição, bolsa) e quem mais acompanha a conta |
| Quando postar | Dia da semana e horário com maior alcance |
| O que funciona | Formatos e características de legenda que performam melhor |
| Público | Cidades, idade, gênero e alcance de não seguidores |

O que a Meta **não** libera por meios oficiais: a lista de quem curtiu e a lista de quem seguiu.

## Painel de anúncios

| Seção | O que responde |
|---|---|
| Resumo | Quanto foi investido, quantos contatos vieram e quanto custou cada um |
| Atenção e oportunidades | Campanhas gastando sem resultado, criativos cansando ou caros, criativos baratos com pouca verba |
| Contatos gerados | Contatos por dia (formulário e conversa) e custo por contato |
| Quando os contatos chegam | Horário e dia da semana com mais contatos, para escalar o comercial |
| Campanhas | Semáforo de custo por contato comparado com a média da conta |
| Criativos | Cada arte com investimento, contatos, custo e cliques |
| O que o lead viu | Texto dos anúncios que mais trouxeram contatos |
| Do anúncio ao contato | Funil: viram → clicaram → viraram contato |
| Quem vira contato | Estados, idade, gênero e posicionamento (Feed, Stories, Reels…) |

**Contato** = formulário preenchido + conversa iniciada (WhatsApp/Direct) por um anúncio.

As miniaturas dos criativos ficam em `data/ads/thumbs/` **sem criptografia** (os links da Meta expiram, por isso são baixadas). São as mesmas artes que já aparecem publicamente na Biblioteca de Anúncios da Meta. Números de investimento e resultado ficam criptografados.

## Aba Cursos

Mesmo conteúdo da planilha semanal de campanhas, atualizado sozinho:

- Curso, cidade (Ribeirão Preto / Rio Preto) e tipo de criativo são identificados **pelo nome da campanha e do anúncio**. As regras ficam no início do `<script>` do `cursos.html` (`CURSO_RULES`, `CIDADE_RULES`, `TIPO_RULES`). Ao criar um curso novo, siga o padrão de nome atual (ex.: `GEST. FINANCEIRA RIB_PRETO - ...`) ou acrescente uma regra.
- Campanhas de alcance/engajamento ficam fora dos rankings de custo por lead (`CURSOS_SEM_LEAD`).
- **Baixar planilha** exporta um CSV no mesmo formato da planilha de campanhas (separador `;`, vírgula decimal).
- O histórico semanal fica em `data/ads_weekly.enc.json`. A primeira coleta busca tudo desde `HIST_START` (padrão 2024-01-01) e pode levar alguns minutos.

## Aba Comercial (RD Station CRM)

- Secret `RD_CRM_TOKEN` com o token do RD Station CRM. O coletor (`scripts/collect_rd.py`) **não guarda** nome, telefone, e-mail nem contatos dos alunos.
- **Matrícula = negócio marcado como venda** (usa o campo "Data da Venda", quando preenchido; senão, a data de fechamento).
- **Custo por matrícula** = investimento na Meta (curso × cidade) ÷ matrículas com fonte de anúncio da Meta (`FONTE_META` no `comercial.html`).
- Curso vem da **campanha** do negócio no RD; cidade vem do campo **Praça**.
- **Esfriando** = negócio em andamento sem atividade há mais de 7 dias (`DIAS_ESFRIANDO`).
- Ranking por consultor: desligado. Para ligar, crie a variável `RD_POR_CONSULTOR=1` no passo do RD do workflow.
- Na primeira coleta, o log mostra os campos que o RD devolve (sem valores), para conferir se Praça e Data da Venda foram encontrados.

## Secrets necessários

**Settings → Secrets and variables → Actions**

| Nome | Valor |
|---|---|
| `IG_ACCESS_TOKEN` | token do usuário do sistema |
| `IG_USER_ID` | ID da conta do Instagram |
| `DASHBOARD_PASSWORD` | senha que o time vai usar para abrir os painéis |
| `AD_ACCOUNT_ID` | ID da conta de anúncios (só o número). Sem ele, o painel de anúncios não é coletado |

O token precisa das permissões `instagram_basic`, `instagram_manage_insights`, `pages_show_list`, `pages_read_engagement`, `ads_read` e (para a seção Pessoas) `instagram_manage_comments`, e a conta de anúncios precisa estar atribuída ao usuário do sistema com **Ver desempenho**. Se preferir um token separado para anúncios, crie o secret `META_ADS_TOKEN`.

Sem `DASHBOARD_PASSWORD`, o painel funciona sem senha, mas a seção **Pessoas** fica desativada.

**Trocar a senha depois apaga o histórico** (os dados antigos foram criptografados com a senha antiga). Escolha uma senha definitiva.

## Relatórios

Os dois painéis têm o botão **Relatório** no topo: escolha 7 ou 30 dias e

- **Baixar PDF**: abre a janela de impressão; escolha "Salvar como PDF".
- **Copiar resumo em texto**: pronto para colar no WhatsApp ou no e-mail.

## Ajustes

- **Temas da seção "Para o comercial":** lista `THEMES` no início do `<script>` do `index.html`. Cada post entra no primeiro tema que bater.
- **Mensagem pronta do WhatsApp:** `MSG_TEMPLATE`, logo abaixo dos temas.
- **Metas do mês (anúncios):** `METAS` no início do `<script>` do `anuncios.html`. Ex.: `{ contatos: 600, investimento: 20000, custo_por_contato: 35 }`.
- **Assuntos dos anúncios:** lista `AD_THEMES`, logo abaixo das metas.
- **Palavras que indicam lead nos comentários:** `INTENT_RULES` em `scripts/collect.py`.
- **Frequência da coleta:** linha `cron` em `.github/workflows/collect.yml` (horário UTC).

## Seções que dependem de permissão

O coletor descobre sozinho o que o token consegue ler. Se algo não estiver liberado, aquela seção mostra um aviso e o resto continua funcionando.

- **Comentários** podem exigir a permissão `instagram_manage_comments` no token.
- **Público** só aparece para contas com 100+ seguidores.

## Se a coleta falhar

Abra a execução com X vermelho em **Actions** e veja o passo "Coletar dados do Instagram".

| Mensagem | O que fazer |
|---|---|
| `defina os secrets...` | Confira os nomes dos Secrets. |
| `não consegui ler o perfil` | Token sem acesso à conta ou ID errado. |
| `não foi possível abrir os dados com a senha atual` | A `DASHBOARD_PASSWORD` mudou. Volte a senha antiga ou apague `data/state.enc.json`. |
| `código 190` | Token inválido. Gere outro e atualize o Secret. |
| `[anúncios] ERRO: o token não consegue ler a conta de anúncios` | Falta `ads_read` no token ou a conta de anúncios não foi atribuída ao usuário do sistema. |

Se a coleta de anúncios falhar, o painel do Instagram é publicado normalmente (o passo de anúncios fica com aviso amarelo em Actions).
