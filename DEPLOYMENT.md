# Deployment

This is a Streamlit app. GitHub hosts the code, and a Python app host such as Streamlit Community Cloud, Render, Railway, or a VPS runs it.

## 1. Prepare The Repo

```powershell
git init
git add .
git commit -m "Build ProofHub task orchestrator"
```

Create an empty GitHub repository, then push:

```powershell
git branch -M main
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

Do not commit ProofHub API keys. `.streamlit/secrets.toml` is ignored by git.

## 2. Deploy On Streamlit Community Cloud

1. Go to `https://share.streamlit.io`.
2. Choose **New app**.
3. Select your GitHub repo and branch.
4. Set main file path:

```text
app.py
```

5. Deploy.

## 3. Configure Secrets

The app can accept the API key in the password field at runtime. For team deployments, add this in Streamlit Cloud **App settings > Secrets**:

```toml
PROOFHUB_API_KEY = "your-proofhub-api-key"
```

The app reads this value automatically and still lets users override it in the password field for a session.

## 4. Runtime Defaults

The app currently defaults to:

```text
API base URL: https://smarketers.proofhub.com/api/v3
Company URL: https://smarketers.proofhub.com
Project ID: 9572720073
Tasklist ID: 271269310285
```

These can be edited in the sidebar.

## 5. Local Production Test

```powershell
pip install -r requirements.txt
streamlit run app.py
```

Open:

```text
http://localhost:8501
```
