# Multi Camera Recorder with Astro Control

Repozytorium: https://github.com/konradmiroslawgorski/multi_camera_recorder_with_astro_control

## Środowisko uruchomieniowe (VM)

Rejestrator działa na maszynie wirtualnej z Ubuntu. Zanim zaczniesz pracę
(wdrożenie, restart usług, podgląd logów), **uruchom VM serwera**:

- adres IP: `192.168.196.10`
- user: ubuntu, password: kcb
- alias SSH: `ubuntu-mv` (wpis w lokalnym pliku `.ssh/config` na Windowsie,
  zwykle `C:\Users\<użytkownik>\.ssh\config`)

Połączenie: `ssh ubuntu-mv`. Ten sam alias wybierzesz w VS Code (Remote - SSH).
Gdy VM jest wyłączona, kamery nie są nagrywane i nie przychodzą powiadomienia.

## Astronomical recording window

Camera services use `config/recording_config.yaml` to record from the configured
time before sunrise through the configured time after sunset. Sunrise and sunset
are calculated locally with Astral for the configured location and timezone;
the recorder does not make HTTP requests.

The default window is one hour before sunrise through one hour after sunset.
Change `recording_window.start` and `recording_window.stop` in the YAML file to
adjust it. The recorder checks the local schedule every 30 seconds while waiting,
starts FFmpeg if the service starts during the window, and sends FFmpeg SIGINT at
the end of the window so it can finalize the current segment.

Install the declared Python dependencies into `venv_motion` with
`venv_motion/bin/pip install -r requirements.txt`. Restart the camera services
after changing the configuration, for example:
`sudo systemctl restart camera@POE1 camera@POE_BRAMA`.

## Powiadomienia push (ntfy)

Rejestrator wysyła powiadomienia przez [ntfy](https://ntfy.sh) (push na iPhone
przez aplikację ntfy), bez dodatkowych zależności. Format:
`data czas kamera opis`, np. `2026-10-23 13:06:40 POE1 start nagrywania filmu [10-00-00.mkv]`.

Zdarzenia:
- **start / stop nagrywania** z nazwą pliku `.mkv` (pierwszy plik po starcie, ostatni przy zatrzymaniu),
- **status** (konfiguracja + dane astro: wschód, zachód, okno, z adnotacją „astro OK"):
  tylko rano po starcie nagrywania otwartym przez astro i wieczorem po zatrzymaniu
  na końcu okna. Restart usługi lub nagrywania w ciągu dnia nie wysyła statusu,
- **błędy** (nieoczekiwane wyjście FFmpeg, brak nowego pliku): pierwszy od razu,
  kolejne w ciągu godziny (`error_group_minutes`) w jednym zbiorczym powiadomieniu.

Konfiguracja: sekcja `notifications` w `config/recording_config.yaml`. Nazwa tematu
działa jak hasło (publiczny serwer), więc nie trzymamy jej w repozytorium:

1. `cp config/notify.env.example config/notify.env` i ustaw losowy `NTFY_TOPIC`
   (plik jest w `.gitignore`; można też użyć zmiennej środowiskowej).
2. W aplikacji ntfy na iPhone zasubskrybuj ten sam temat.
3. `sudo systemctl restart camera@POE1 camera@POE_BRAMA`.

Bez `NTFY_TOPIC` powiadomienia są wyłączone (ostrzeżenie w logu), a nagrywanie działa normalnie.
Awaria sieci nigdy nie blokuje nagrywania.

## Dostęp do nagrań (sieć lokalna)

VM ma adres `192.168.196.10` (VMware NAT), dostępny z laptopa z Windowsem.
Zdalny dostęp spoza domu nie jest skonfigurowany. **Nie wystawiamy SSH ani SMB do internetu.**

Struktura plików: `/opt/cameras/recordings/<KAMERA>/<RRRR-MM-DD>/<GG-MM-SS>.mkv`.

### SMB (tylko do odczytu)

Udział `cameras` jest zdefiniowany w `/etc/samba/smb.conf`
(`path = /opt/cameras`, `read only = yes`, `valid users = ubuntu`).
Hasło Samby jest osobne od hasła systemowego: `sudo smbpasswd -a ubuntu`.

- Windows: `\\192.168.196.10\cameras` (login `ubuntu`).
- VLC (Windows / iPhone w tej samej sieci): SMB, serwer `192.168.196.10`,
  udział `cameras`, użytkownik `ubuntu`. Dalej: `recordings` → kamera → data → plik `.mkv`.

SFTP w VLC na iOS wracał do menu bez błędu, więc nie jest zalecany.

### Opcjonalnie: HTTP tylko do odczytu (nginx)

Nagrania na porcie `8080`, bez możliwości zapisu, dostępne z LAN VM:

```bash
sudo ln -s /home/ubuntu/projects/mcrwac/config/nginx/recordings.conf /etc/nginx/conf.d/recordings.conf
sudo nginx -t && sudo systemctl reload nginx
curl -sI http://127.0.0.1:8080/recordings/ | head -1    # oczekiwane: HTTP/1.1 200 OK
```

Lista plików: `http://192.168.196.10:8080/recordings/`. W VLC: **Sieć** →
**Otwórz strumień sieciowy** i adres pliku, np.
`http://192.168.196.10:8080/recordings/POE1/2026-10-23/13-35-03.mkv`.

### Diagnostyka

| Objaw | Sprawdź |
|---|---|
| SMB „access denied” | złe hasło Samby, ustaw nowe: `sudo smbpasswd -a ubuntu` |
| Brak plików | czy VM jest uruchomiona i usługi `camera@...` działają |
