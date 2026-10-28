import os
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

load_dotenv()
project = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
                          credential=DefaultAzureCredential())
client = project.get_openai_client()
r = client.responses.create(model=os.environ["FOUNDRY_MODEL"], input="Reply with the single word ok")
print(r.output_text)