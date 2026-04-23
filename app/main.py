from anthropic import Anthropic
import os
from dotenv import load_dotenv

load_dotenv()

client = Anthropic() 

res = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=50,
    messages=[{"role": "user", "content": "Hello"}]
)

print(res.content[0].text)