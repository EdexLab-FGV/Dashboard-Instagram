# Dashboard Instagram

Dashboard que se atualiza sozinho com os seguidores e as curtidas da sua conta do Instagram.

- **GitHub Actions** coleta os dados da Instagram Graph API a cada 6 horas.
- Os dados ficam salvos em `data/` (o próprio repositório vira o banco de dados).
- **GitHub Pages** publica o dashboard em `https://SEU-USUARIO.github.io/NOME-DO-REPO/`.

A API do Instagram só informa o total **atual** de seguidores e curtidas. Os "novos seguidores" e "novas curtidas" são calculados comparando uma coleta com a anterior, então o histórico começa no dia em que você ligar o projeto.

---

## Instalação (uns 10 minutos)

### 1. Crie o repositório

1. No GitHub, clique em **New repository**.
2. Dê um nome (ex.: `dashboard-instagram`) e deixe **Public**.
   > O GitHub Pages gratuito exige repositório público. Os números do dashboard (seguidores, curtidas) ficam visíveis para quem tiver o link. O token **não** fica exposto, ele vai nos Secrets.
3. Envie todos os arquivos desta pasta para o repositório, **incluindo a pasta `.github`** (ela fica oculta em alguns sistemas).
   - Pelo site: **Add file → Upload files** e arraste a pasta inteira.

### 2. Cadastre os Secrets

No repositório: **Settings → Secrets and variables → Actions → New repository secret**.

| Nome | Valor |
|---|---|
| `IG_ACCESS_TOKEN` | o token gerado no Graph API Explorer |
| `IG_USER_ID` | o ID da conta do Instagram (Business Suite → Configurações da empresa → Contas → Contas do Instagram) |

### 3. Ative o GitHub Pages

**Settings → Pages → Build and deployment → Source: GitHub Actions**.

### 4. Rode a primeira coleta

1. Aba **Actions** → workflow **Coletar dados e publicar dashboard**.
2. Clique em **Run workflow**.
3. Quando ficar verde, o link do dashboard aparece no resumo da execução (e em Settings → Pages).

Depois disso, ele roda sozinho a cada 6 horas.

---

## O que o dashboard mostra

- Seguidores atuais e variação nas últimas 24h
- Novos seguidores e novas curtidas no período (7, 30, 90 dias ou tudo)
- Engajamento médio por post ((curtidas + comentários) / seguidores)
- Evolução de seguidores ao longo do tempo
- Saldo de seguidores por dia e novas curtidas por dia
- Curtidas e comentários dos últimos 20 posts
- Top 5 posts e engajamento médio por formato (Reels, carrossel, imagem, vídeo)

---

## Ajustes

- **Frequência da coleta:** edite a linha `cron` em `.github/workflows/collect.yml`. O horário é UTC (Brasília = UTC−3). Ex.: a cada 3 horas → `"17 */3 * * *"`.
- **Quantidade de posts acompanhados:** variável `MAX_POSTS` no workflow (padrão 100).
- **Token gerado com login do Instagram** (em vez do Facebook): adicione `GRAPH_HOST: graph.instagram.com` no bloco `env` do passo de coleta.

---

## Se a coleta falhar

Abra a execução com X vermelho em **Actions** e veja o passo "Coletar dados do Instagram". O erro vem da própria API:

| Mensagem | O que fazer |
|---|---|
| `defina os secrets...` | Confira os nomes dos Secrets (exatamente `IG_ACCESS_TOKEN` e `IG_USER_ID`). |
| `OAuthException 190` | Token inválido ou expirado. Gere um novo no Graph API Explorer e atualize o Secret. |
| `OAuthException 10` / `200` | Falta permissão. Gere o token de novo marcando a conta do Instagram e `instagram_basic`. |
| `100` / `Unsupported get request` | ID da conta errado. Confira o `IG_USER_ID`. |

Quando uma coleta falha, os dados anteriores **não** são apagados. O dashboard continua mostrando a última coleta boa.

> O GitHub pausa workflows agendados de repositórios sem nenhuma atividade por 60 dias. Como cada coleta gera um commit, isso normalmente não acontece. Se acontecer, é só reativar em Actions.

---

## Rodar localmente (opcional)

```bash
export IG_ACCESS_TOKEN="seu_token"
export IG_USER_ID="seu_id"
python3 scripts/collect.py
python3 -m http.server 8000   # abra http://localhost:8000
```
