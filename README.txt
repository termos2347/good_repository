## python 3.11


введется разработка 
инструкция по установке и описание работы появится позже

```
git clone https://github.com/termos2347/good_repository
```

```yaml
services:
    fullfeedrss:
        image: 'heussd/fivefilters-full-text-rss:latest'
        environment:
            # Leave empty to disable admin section
            - FTR_ADMIN_PASSWORD=1234 #CHANGE PASSWORD
        volumes:
            - 'rss-cache:/var/www/html/cache/rss'
        ports:
            - '80:80'
volumes:
    rss-cache:
```
