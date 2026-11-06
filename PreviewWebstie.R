# 1. Start the Jekyll server in the RStudio Terminal
rstudioapi::terminalExecute("bundle exec jekyll serve")

# 2. Wait 4 seconds to give Jekyll time to compile the site
Sys.sleep(4)

# 3. Open the live site in the RStudio Viewer tab
rstudioapi::viewer("http://127.0.0.1:4000")