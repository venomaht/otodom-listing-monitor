## Uruchomienie

```bash
git clone https://github.com/venomaht/otodom-listing-monitor.git
cd otodom-listing-monitor
docker compose up --build


# Otodom Listing Monitor

Aplikacja w Pythonie do automatycznego monitorowania ogłoszeń mieszkań z Otodom.pl dla wybranej lokalizacji.

Projekt pobiera listę ogłoszeń, zapisuje je do relacyjnej bazy danych PostgreSQL oraz przy kolejnych uruchomieniach wykrywa zmiany:
- nowe oferty,
- oferty pierwszy raz znalezione przez system,
- zmiany ceny,
- potencjalnie usunięte / zarchiwizowane ogłoszenia.

Aplikacja uruchamiana jest przez Docker Compose. Po uruchomieniu dostępny jest również prosty interfejs Streamlit do podglądu danych i ręcznego uruchamiania pobierania dostepny pod adresem: http://localhost:8501

---

Aplikacja składa się z trzech kontenerów:
otodom_db — baza danych PostgreSQL,
otodom_ui — interfejs Streamlit,
otodom_worker — automatyczny worker cyklicznie uruchamiający monitoring.
Domyślnie monitorowane miasto to Łódź.


KONFIGURACJA:
Konfiguracja znajduje się w pliku .env.example. Z najwazniejszymi zmiennymi:
SCRAPE_CITY — miasto, dla którego worker automatycznie uruchamia monitoring.
SCRAPE_INTERVAL_MINUTES — odstęp między automatycznymi cyklami monitoringu.
RUN_ON_STARTUP — czy wykonać monitoring od razu po starcie kontenera.
ENABLE_FULL_SCRAPE — czy wykonywać pełne pobranie ogłoszeń dla miasta.
ENABLE_LATEST_24H_SCRAPE — czy wykonywać dodatkowe pobranie ofert z ostatnich 24h.

INTERFACE UZYTKOWNIKA:
Interface został przygotowany w Streamlit.
Dostępne jest 20 największych miast Polski. Lista miast znajduje się w dropdownie i jest posortowana alfabetycznie. Domyślnie wybrana jest Łódź.

W UI dostępne są widoki:
- pełny scrape miasta,
- scrape ofert z ostatnich 24h,
- realnie nowe oferty (new_offer),
- oferty pierwszy raz znalezione przez system (newly_found),
- ostatnie zmiany,
- pełna baza ogłoszeń,
- historia eventów,
- historia uruchomień scrapera

LOGIKA DZIALANIA:
Pobieranie danych opiera się na publicznych wynikach wyszukiwania Otodom.pl.
Dla wybranego miasta aplikacja buduje URL wyników wyszukiwania z limitem 72 ofert na stronę oraz przechodzi po kolejnych numerach stron.

Proces pełnego pobierania wygląda następująco:

1. Aplikacja pobiera strony wyników Otodom dla wybranego miasta.
2. Strony są pobierane w batchach po 5 jednocześnie, żeby przyspieszyć działanie.
3. Z każdej strony parsowany jest HTML i wyciągane są podstawowe dane ogłoszeń:
ID ogłoszenia,
tytuł,
cena,
cena za m²,
lokalizacja,
powierzchnia,
liczba pokoi,
URL.
4. Po pierwszym przebiegu aplikacja wykonuje drugi przebieg pobierania.
5. Wyniki obu przebiegów są scalane po unikalnym ID ogłoszenia.
6. Dane są zapisywane do PostgreSQL.
7. Przy kolejnym uruchomieniu aplikacja porównuje aktualnie pobrane dane ze stanem zapisanym w bazie.
8. Na podstawie porównania tworzone są eventy zmian.

*Uwaga* Podwójne pobieranie zostało dodane celowo, ponieważ przy web scrapingu wyników Otodom pojedynczy przebieg może nie zwrócić stabilnie 100% ofert. Drugi przebieg zmniejsza ryzyko pominięcia ogłoszeń.


RODZAJE WYKRYWANYCH ZMIAN:

Aplikacja rozróżnia kilka typów eventów.

newly_found - Oferta oznaczona jako newly_found to ogłoszenie, którego wcześniej nie było w lokalnej bazie danych podczas pełnego scrapowania miasta.
Nie oznacza to automatycznie, że oferta została świeżo dodana na Otodom. Oznacza jedynie, że system zobaczył ją pierwszy raz.

new_offer - Oferta oznaczona jako new_offer to ogłoszenie znalezione w osobnym trybie pobierania ofert z ostatnich 24h.
Ten tryb używa URL Otodom z parametrem daysSinceCreated=1 oraz sortowaniem po najnowszych ofertach. Następnie wyniki są porównywane z istniejącą bazą danych.
To jest najlepszy wskaźnik realnie nowych ofert.

price_change - Jeżeli oferta istnieje już w bazie, ale podczas kolejnego pobrania jej cena jest inna, aplikacja zapisuje event price_change.
W evencie zapisywana jest stara i nowa wartość ceny.

removed - Oferta nie jest oznaczana jako usunięta tylko dlatego, że nie pojawiła się w aktualnych wynikach wyszukiwania.
Najpierw traktowana jest jako potencjalnie usunięta. Następnie aplikacja otwiera URL szczegółowy tej oferty i sprawdza HTML strony. Dopiero gdy na stronie pojawią się sygnały wskazujące, że ogłoszenie jest niedostępne, archiwalne lub usunięte, oferta zostaje oznaczona jako removed.
Takie podejście ogranicza liczbę fałszywych oznaczeń usuniętych ofert.

STRUKTURA BAZY DANYCH:

Projekt używa PostgreSQL jako relacyjnej bazy danych oraz SQLAlchemy jako ORM.

Główne tabele: 

listings - przechowuje aktualny stan ogłoszeń i zawiera między innymi:
- ID ogłoszenia z Otodom,
- miasto
- URL
- opis
- cenę
- cenę za m²
- lokalizację
- powierzchnię
- status aktywności
- datę pierwszego i ostatniego znalezienia

listing_snapshots - przechowuje historyczne snapshoty danych ogłoszenia z konkretnych uruchomień scrapera. Dzięki temu można sprawdzić, jak dane wyglądały w momencie konkretnego pobrania.

listing_events - przechowuje historię wykrytych zmian jako typy eventów: newly_found; new_offer; price_change; removed 

scrape_runs - przechowuje historię uruchomień scrapera i zawiera:
- datę startu
- datę zakończenia
- miasto
- status
- liczbę znalezionych ofert
- liczbę nowych ofert
- liczbę zmian cen
- liczbę usuniętych ofert
- ewentualny komunikat błędu


DECYZJE PROJEKTOWE:

1. Program batchuje pobieranie danych z kilku stron jednoczesnie, w celu przyspieszenia dzialania programu
2. Program powtarza cykl pobierania danych w celu unikniecia pominiecia ofert
3. Program sprawdza kazda oferte zidentyfikowana jako brakujaca poprzez url w celu unikniecia blednego oznaczenia oferty jako usunieta
4. Program sprawdza oferty z ostatnich 24h jako osobny cykl, w celu unikniecia blednego oznaczenia oferty jako nowej
5. Program kategoryzuje oferty w wiekszej ilosci anizeli nowe, usuniete, zmieniona cena, poniewaz moja metoda nie jest idealna i wystepuja w niej bledy ktore probowalem eliminowac poprzez powyzsze metody. Projekt opiera się na analizie HTML publicznych stron Otodom, dlatego nie jest to metoda idealna.
6. Projekt został przygotowany z wykorzystaniem narzędzi AI jako wsparcia technicznego przy implementacji w Pythonie. Skupiłem się przede wszystkim na logice procesu, walidacji danych, testowaniu rezultatów oraz iteracyjnym poprawianiu działania aplikacji.
