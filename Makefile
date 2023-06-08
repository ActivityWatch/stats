output/sponsors.html: output/sponsors.md
	markdown2 $< -x tables > $@

output/sponsors.md: analyze_sponsors.py
	python3 analyze_sponsors.py > $@
