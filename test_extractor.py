from extractor import Extractor, load_providers

print("[*] Loading providers...")
providers = load_providers("provider.json")
extractor = Extractor(providers)


mock_html = """
<html>
<body>
    <h1>Welcome to my page</h1>
    <p>Contact me at <b>ghost.rider [at] gmail [dot] com</b> for business.</p>
    <p>Ignore the fake: test@example.com and the image logo@2x.png</p>
    <p>My old email is webmaster@target.com (do not email).</p>
    
    <!-- Social Links -->
    <a href="https://twitter.com/ghost_rider99">Twitter</a>
    <a href="https://github.com/ghost_rider99">GitHub</a>
    <a href="https://github.com/about">About GitHub</a> <!-- Should be filtered as noise -->
    <a href="https://t.me/ghost_hacker_99">Telegram</a>
    <a href="https://bsky.app/profile/ghostrider.bsky.social">Bluesky</a>
    
    <a href="mailto:admin@hackerlab.com?subject=Hire%20Me">Hire Me</a>
</body>
</html>
"""

print("\n[*] Extracting Data...")

result = extractor.extract(
    mock_html, 
    base_url="https://ghostrider.com", 
    current_provider="github", 
    current_handle="ghost_rider99" 
)

print("\n[+] --- Extraction Results ---")
print(f"Emails Found: {result.emails}")
print(f"Handles Found: {result.handles}")
print(f"Stats: {result.stats}")
