"""CPA mediante controles normales del navegador; nunca retira disabled."""
import re
from urllib.parse import quote, urlparse

BASE_URL = 'https://cp.champrosports.com'


class CPAError(ValueError):
    pass


def status_code(label):
    match = re.match(r'^\s*(\d{1,4})\s*[–—-]', label)
    if not match:
        raise CPAError('Estado CPA no reconocido; no se modificó la línea.')
    return int(match[1])


def profile_order(order):
    order = str(order).strip().upper()
    if order.isdigit():
        order = 'SO-' + order
    if not re.fullmatch(r'SO-\d+', order):
        raise CPAError('Número de orden inválido; se esperaba SO-número o número.')
    return order


class CPABrowser:
    def __init__(self, config):
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.support.ui import WebDriverWait
        options = webdriver.ChromeOptions()
        if config.get('headless', False):
            options.add_argument('--headless=new')
        options.add_argument('--window-size=1600,1000')
        options.add_experimental_option('prefs', {'credentials_enable_service': False, 'profile.password_manager_enabled': False})
        if config.get('chrome_binary'):
            options.binary_location = config['chrome_binary']
        service = Service(executable_path=config['chromedriver']) if config.get('chromedriver') else Service()
        self.driver = webdriver.Chrome(service=service, options=options)
        self.driver.set_page_load_timeout(45)
        self.wait = WebDriverWait(self.driver, 30)
        self.config = config

    def close(self):
        self.driver.quit()

    def _one(self, parent, by, selector):
        matches = [e for e in parent.find_elements(by, selector) if e.is_displayed()]
        if len(matches) != 1:
            raise CPAError('Control CPA ausente o ambiguo. Revisa los selectores de configuración.')
        return matches[0]

    def _origin(self):
        url = urlparse(self.driver.current_url)
        if url.scheme != 'https' or url.netloc != 'cp.champrosports.com':
            raise CPAError('CPA redirigió a otro sitio; operación detenida.')

    def login(self, email, password):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        self.driver.get(BASE_URL + '/login')
        selector = self.config.get('login_email_selector', 'input[placeholder="Email Address"]')
        self.wait.until(lambda d: any(e.is_displayed() for e in d.find_elements(By.CSS_SELECTOR, selector)))
        self._origin()
        field = self._one(self.driver, By.CSS_SELECTOR, selector)
        secret = self._one(self.driver, By.CSS_SELECTOR, self.config.get('login_password_selector', 'input[type="password"]'))
        field.clear(); field.send_keys(email)
        secret.clear(); secret.send_keys(password); secret.send_keys(Keys.ENTER)
        self.wait.until(lambda d: '/login' not in urlparse(d.current_url).path.lower())
        self._origin()

    def _line(self, job):
        from selenium.webdriver.common.by import By
        # Comparación textual exacta: los IDs nunca pasan por float/int.
        found = []
        for row in self.driver.find_elements(By.CSS_SELECTOR, 'tr'):
            cells = row.find_elements(By.XPATH, './td')
            if cells and cells[0].text.strip() == str(job['ID_CPA']).strip() and row.is_displayed():
                found.append((row, cells))
        if not found:
            return False
        if len(found) != 1:
            raise CPAError('ID CPA duplicado en el perfil; no se modificó ninguna línea.')
        row, cells = found[0]
        if len(cells) < 2 or cells[1].text.strip().upper() != str(job['LINEA']).strip().upper():
            raise CPAError('El ID CPA no corresponde a la línea solicitada.')
        # En el HTML proporcionado hay otros dos selects; solo el principal
        # contiene 91 / 0910. No dependemos de un índice ni del estilo width.
        matches = []
        for select in row.find_elements(By.TAG_NAME, 'select'):
            if any(o.get_attribute('value') == '91' and status_code(o.text) == 910
                   for o in select.find_elements(By.TAG_NAME, 'option') if o.get_attribute('value') == '91'):
                matches.append(select)
        if len(matches) != 1:
            raise CPAError('No se encontró un único selector principal de estado.')
        return row, matches[0]

    def _read(self, job):
        from selenium.webdriver.support.ui import Select
        self._origin()
        expected = '/Profile/' + profile_order(job['# DE ORDEN'])
        if urlparse(self.driver.current_url).path.rstrip('/').lower() != expected.lower():
            raise CPAError('No está abierto el perfil esperado; revisa la sesión CPA.')
        row, element = self.wait.until(lambda d: self._line(job))
        selected = Select(element).first_selected_option
        return row, element, status_code(selected.text), selected.text.strip()

    @staticmethod
    def _skip(code, label):
        if code == 910:
            return 'YA EN 910', 'Sin cambios: la línea ya está en 0910 – QC After Cut.'
        if code > 910:
            return 'SUPERIOR A 910', 'Sin cambios: estado actual superior al destino: ' + label
        return None

    def update(self, job, before_save=lambda: None):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import Select
        self.driver.get(BASE_URL + '/Profile/' + quote(profile_order(job['# DE ORDEN']), safe=''))
        row, element, code, label = self._read(job)
        skipped = self._skip(code, label)
        if skipped:
            return skipped
        if not element.is_enabled():
            selector = self.config.get('edit_button_selector')
            if not selector:
                raise CPAError('Edición deshabilitada: configura edit_button_selector con el control real de CPA.')
            button = self._one(self.driver, By.CSS_SELECTOR, selector)
            if not button.is_enabled():
                raise CPAError('La cuenta CPA no tiene habilitada la edición.')
            button.click()
            self.wait.until(lambda d: self._line(job) and self._line(job)[1].is_enabled())
        # Releer después de entrar en edición, antes de cambiar nada.
        row, element, code, label = self._read(job)
        skipped = self._skip(code, label)
        if skipped:
            return skipped
        before_save()
        Select(element).select_by_value('91')
        # Solo guardar la tarjeta que contiene la línea; no todos los perfiles.
        card = row.find_element(By.XPATH, './ancestor::div[contains(concat(" ",normalize-space(@class)," ")," card ")][1]')
        save = self._one(card, By.XPATH, './/button[normalize-space(.)="Save Line Changes"]')
        self.wait.until(lambda d: save.is_enabled())
        before_save()
        save.click()
        # Esperar a que la aplicación termine el guardado antes del reload.
        from selenium.common.exceptions import StaleElementReferenceException
        def saved(d):
            try:
                return not save.is_enabled() and not self._line(job)[1].is_enabled()
            except StaleElementReferenceException:
                return False
        self.wait.until(saved)
        self.driver.refresh()
        _, _, actual, _ = self._read(job)
        if actual != 910:
            raise CPAError('Después de guardar y recargar no se confirmó 0910. Se comprobará CPA antes del próximo intento.')
        return 'ACTUALIZADA A 910', 'Guardado en CPA y confirmado tras recargar: 0910 – QC After Cut.'
