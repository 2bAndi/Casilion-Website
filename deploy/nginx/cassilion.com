# /etc/nginx/sites-available/cassilion.com
# One certificate for all four names (certbot certonly --webroot -w /var/www/html,
# see docs/server-setup.md, section 2):
#   cassilion.com   cassilion.com, www.cassilion.com, cassilion.de, www.cassilion.de

# Main site -------------------------------------------------------------------
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name cassilion.com;

    ssl_certificate /etc/letsencrypt/live/cassilion.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cassilion.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    location /.well-known/acme-challenge/ { root /var/www/html; }

    include snippets/cassilion-app.conf;
}

# Other names of the site: permanent redirect, path and query kept ----------
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name www.cassilion.com cassilion.de www.cassilion.de;

    ssl_certificate /etc/letsencrypt/live/cassilion.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cassilion.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://cassilion.com$request_uri; }
}

# Port 80: certificate checks, everything else to HTTPS on the same name --------
server {
    listen 80;
    listen [::]:80;
    server_name cassilion.com www.cassilion.com cassilion.de www.cassilion.de;

    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://$host$request_uri; }
}
