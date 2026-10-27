# Painel Instagram EdexLab

Painel que se atualiza sozinho com os dados da conta do Instagram, pensado para o time comercial.

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

## Secrets necessários

**Settings → Secrets and variables → Actions**

| Nome | Valor |
|---|---|
| `IG_ACCESS_TOKEN` | token do usuário do sistema |
| `IG_USER_ID` | ID da conta do Instagram |
| `DASHBOARD_PASSWORD` | senha que o time vai usar para abrir o painel |

Sem `DASHBOARD_PASSWORD`, o painel funciona sem senha, mas a seção **Pessoas** fica desativada.

**Trocar a senha depois apaga o histórico** (os dados antigos foram criptografados com a senha antiga). Escolha uma senha definitiva.

## Ajustes

- **Temas da seção "Para o comercial":** lista `THEMES` no início do `<script>` do `index.html`. Cada post entra no primeiro tema que bater.
- **Mensagem pronta do WhatsApp:** `MSG_TEMPLATE`, logo abaixo dos temas.
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
