LOCAL SURVEY TEST AGENT — WINDOWS SETUP

LOCAL MOCK WORKFLOW
- Password-protected dashboard, visible Playwright browser, form field explorer, review/validation, local SQLite history aur Excel export.
- Browser URL sirf localhost/loopback HTTP(S) par ho sakta hai; baaki requests block hoti hain.
- Consent controls browser mein manually handle karein.
- Public ya live medical survey support nahi hai. Synthetic medical answers kabhi live site par submit na karein.

SETUP AUR RUN
1. Python 3.10 ya newer install karein; Python launcher (py) enable karein.
2. setup.bat chalayein; yeh dependencies, Chromium runtime aur password hash set karta hai.
3. run_agent.bat chalayein, phir http://127.0.0.1:8765 par sign in karein.
4. Local form browser khol kar sample form inspect karein; selections preview, validate aur manually review karein.
5. Aakhri submission ke liye explicit confirmation dabayein. Local record confirmation verified ho to hi SUBMITTED_CONFIRMED dikhaya jayega.
6. Run history se records dekhein; Export Excel (.xlsx) workbook mein browser run details bhi milengi.

SECURITY
- Flask dashboard 127.0.0.1 par bind hota hai; browser outbound loopback ke bahar nahi ja sakta.
- Password hash .env mein hai, plaintext password save nahi hota.
- .env aur .app-secret-key private files hain; share na karein.
- Daily run limit 1 se 6 tak set ho sakti hai; same din ka duplicate naam + URL reject hota hai.
- SUBMITTED_LOCAL ka matlab local SQLite write hai. SUBMITTED_CONFIRMED ke liye local success page aur matching database record dono zaroori hain.

STOP
Minimized server window band karein.
