Nová instalace používá angličtinu a zvuk přes Wi-Fi. V Nastavení → Přenos zvuku lze zvolit Bluetooth; uložené volby zůstávají zachovány. Pro Wi-Fi jednou nainstaluj zvuková zařízení Macu tlačítkem v nastavení a dokonči systémový instalátor. Host se k připravenému Šlimakovi připojí bez přeinstalace služeb Pi.

# DenDenMushi

## Připojení

1. Zapni Šlimaka a Bluetooth na Macu. Obě zařízení potřebují přístup ke stejné Wi-Fi síti.
2. Otevři DenDenMushi a klikni na **Připojit**. Aplikace vyhledá Šlimaka přes Bluetooth, obraz z kamery pak přenáší přes Wi-Fi.
3. Na novém Macu jednou zadej **heslo nebo kód Šlimaka**. Nejde o heslo Macu ani o heslo účtu Raspberry Pi. Příště se automaticky použije uložený přístup.
4. V Google Meet nebo Telegramu vyber **OBS Virtual Camera**. OBS běží na pozadí a musí být nainstalované ve složce Aplikace.

Aplikace zobrazuje živý náhled až 15 snímků/s. Do hovoru se přenáší plný obraz 720p / 30 snímků/s.

## Zvuk

Kamera a zvuk jsou na společné obrazovce. Připojit spustí zvuk přes Wi-Fi, pokud jsou ovladače nainstalované. V hovoru vyber **DenDenMushi Wi-Fi Microphone** a **DenDenMushi Wi-Fi Speakers**. Volba reproduktorů a mikrofonu v aplikaci se ihned použije pro výchozí zařízení Macu; aplikace pro hovory může mít vlastní volbu. Při potížích použij tlačítko pro opakování zvuku.

Hlasitost reproduktorů a zesílení mikrofonu jsou v **Nastavení → Nastavení zvuku superadministrátora**, po připojení a zadání samostatného kódu vlastníka. Přístup vyprší po deseti minutách nebo při odpojení. Uvolněním posuvníku se použije hodnota. Host tento kód k běžnému hovoru nepotřebuje.

OBS Virtual Camera přenáší pouze obraz. Po aktualizaci Pi zapni **Mikrofon Šlimaka** pro zkušební obousměrný zvuk přes Bluetooth. V Telegramu / Meet vyber **DenDenMushi** nebo systémový výchozí mikrofon. Kanál se může otevřít až při zahájení nahrávání v Telegramu. V tomto režimu je zvuk mono v telefonní kvalitě. Vypnutím přepínače obnovíš stereo a předchozí mikrofon, pokud jsi mezitím nezvolil jiný. Aplikace neukládá hovor do souboru. Hlas ověř krátkou zkušební nahrávkou.

Starší službu Pi je potřeba jednou aktualizovat v nastavení aplikace. Vlastník zadá heslo účtu Pi do okna aktualizace; při běžném připojování už není potřeba.

## Jazyk

V **Nastavení → Jazyk aplikace** vyber **Українська**, **Čeština** nebo **English**. Jazyk se změní ihned a uloží se na tomto Macu. Názvy zařízení a hesla se nemění. Dialogy oprávnění macOS používají systémové nastavení jazyka aplikace.

## Heslo a Wi-Fi

- **Změnit heslo Šlimaka** nastaví heslo pro přidání Macu. K uložení této změny je potřeba heslo účtu Pi. Neukládá se a přihlašovací heslo Pi se nemění.
- **Kód pro jiný Mac** zkopíruje přístupový kód pro oprávněného uživatele. Zacházej s ním jako s heslem.
- **Změnit Wi-Fi Šlimaka** nastaví jeho síť přes Bluetooth. Pi 3 potřebuje Wi-Fi 2,4 GHz; izolovaná síť pro hosty může blokovat přenos obrazu do Macu.
- **Resetovat připojení tohoto Macu** odebere klíč kamery tohoto Macu na Pi i uložené místní připojení. Kód Šlimaka zkopíruje do schránky pro zopakování prvního připojení. Wi-Fi a služby Pi zůstanou nastavené.

## Nově sestavené Pi

Vlastník jednou nainstaluje služby v **Nastavení → Připravit nové Pi / aktualizovat službu**. Pi už musí být dostupné přes SSH v místní síti. Zadej jeho adresu, uživatelské jméno a heslo účtu. Ostatním uživatelům připraveného Šlimaka stačí jeho heslo nebo kód.

Tlačítko **Zastavit** zastaví kameru. Zavření okna ponechá aplikaci v řádku nabídek; **Ukončit DenDenMushi** zastaví spravované procesy kamery a aplikaci ukončí.

## Automatické ztišení

Po aktualizaci Pi se výstup ztiší přibližně 2 sekundy po ukončení Bluetooth streamu a znovu zapne při zahájení přehrávání. Ruční ztlumení a hlasitost se uchovávají odděleně. Pokud aplikace nadále posílá ticho v aktivním streamu, výstup zůstává zapnutý. Jednou klikni na „Aktualizovat Pi“ a zadej heslo vlastníka v okně aplikace.

## Reproduktory a mikrofon přes jednu USB kartu (v0.5.4)

Vstup zesilovače připoj k zelenému výstupu USB karty Alza, mikrofon k červenému vstupu téže karty. Aktualizuj službu tlačítkem pro přípravu Pi, potom klikni na „Připojit“. Hlasitost reproduktorů a automatické ztlumení ovládají USB výstup; úroveň mikrofonu ovládá USB vstup. Pro hovor navíc zapni „Mikrofon Šlimaka“ (v0.6.0, zkušební režim).

Pokud Mac po přepnutí ze stereofonního přehrávání nezobrazí mikrofon, aplikace jednou znovu připojí pouze DenDenMushi. Zvuk se krátce přeruší a párování se zachová. Pokud obnova nepomůže, klikni na Zkusit zvuk znovu.
