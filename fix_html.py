import os
import re

directory = r"d:\fftour"

for filename in os.listdir(directory):
    if filename.endswith(".html"):
        filepath = os.path.join(directory, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # Replace getApiUrl
        api_pattern = re.compile(r"<script>\s*window\.getApiUrl = function \(\) \{[\s\S]*?return window\.location\.protocol \+ '//' \+ window\.location\.hostname \+ ':8000/api';\s*\};\s*</script>")
        new_api = """<script>
        window.getApiUrl = function () {
            if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
                return 'http://localhost:8000/api';
            }
            if (window.location.hostname.includes('onrender.com')) return '/api';
            return 'https://gamearenax.onrender.com/api';
        };
    </script>"""
        content = api_pattern.sub(new_api, content)

        # Replace Tailwind CDN
        content = re.sub(r'<script src="https://cdn\.tailwindcss\.com"></script>\s*', '', content)
        
        # Add Tailwind CSS link right before the custom styles.css or lucide
        if 'href="tailwind.css"' not in content:
            content = content.replace('<link rel="stylesheet" href="styles.css">', '<link rel="stylesheet" href="tailwind.css">\n    <link rel="stylesheet" href="styles.css">')
        
        # Remove old tailwind.config
        content = re.sub(r"<script>\s*tailwind\.config = \{[\s\S]*?\}\s*</script>\s*", '', content)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Updated {filename}")
