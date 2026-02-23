from google_auth_oauthlib.flow import InstalledAppFlow
import json
import os
from pathlib import Path

# Define the scopes required for the YouTube Data API
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

def main():
    base_dir = Path(__file__).resolve().parent
    client_secret_file = base_dir / "client_secret.json"
    token_file = base_dir / "token.json"

    print("--- GENERADOR DE TOKEN DE YOUTUBE ---")
    print("1. Ve a Google Cloud Console (https://console.cloud.google.com/)")
    print("2. Asegúrate de tener un proyecto con 'YouTube Data API v3' habilitada.")
    print("3. En 'Credenciales', crea una 'ID de cliente de OAuth 2.0' (Tipo: Aplicación de escritorio).")
    print("4. Descarga el JSON y renómbralo a 'client_secret.json'.")
    print(f"5. Pon ese archivo en esta carpeta: {base_dir}")
    
    if not client_secret_file.exists():
        input("\n⚠️  No encuentro 'client_secret.json'. Pónlo aquí y pulsa ENTER para seguir...")
    
    if not client_secret_file.exists():
        print("❌ Sigues sin poner el archivo. Abortando.")
        return

    # Create the flow using the client secrets file
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_file), SCOPES)

    # Run the flow to retrieve the credentials (opens browser)
    creds = flow.run_local_server(port=0)

    # Save the credentials to token.json
    token_json = creds.to_json()
    
    with open(token_file, 'w', encoding='utf-8') as f:
        f.write(token_json)

    print("\n✅ ¡ÉXITO! Se ha creado el archivo 'token.json'.")
    print("👉 Abre ese archivo, COPIA todo el texto y pégalo en los Secretos de GitHub como 'YOUTUBE_TOKEN_JSON'.")
    print("   (El contenido empieza por { \"token\": ... )")

if __name__ == '__main__':
    main()
