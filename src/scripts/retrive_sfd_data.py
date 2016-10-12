import sys, traceback
from bs4 import BeautifulSoup
import rethinkdb as r
import requests
import time
import geocoder
import datetime
from dateutil.parser import parse as dtparse
from pytz import timezone
r.connect( "localhost", 28015).repl()
la = timezone('America/Los_Angeles')
table = r.db("revealseattle").table("dispatch_log")
dbtable = r.db("revealseattle").table("dispatch_log")
dbtable.delete().run()

def get_todays_dispatches():
    already_geocoded = []
    existing_data = dict([(row['id'], row) for row in dbtable.run()])
    addresses_and_coordinates = dbtable.pluck('address', 'coordinates').run()
    addresses_to_coordinates = dict([(item['address'], item['coordinates']) for item in addresses_and_coordinates if item.get('coordinates')])
    addresses_and_place_names = dbtable.pluck('address', 'place_name').run()
    addresses_to_place_names = dict([(item['address'], item['place_name']) for item in addresses_and_place_names if item.get('place_name', '').strip()])
    addresses_and_assessor_ids = dbtable.pluck('address', 'assessor_id').run()
    addresses_to_assessor_ids = dict([(item['address'], item['assessor_id']) for item in addresses_and_place_names if item.get('assessor_id', '').strip()])
    html = requests.get('http://www2.seattle.gov/fire/realtime911/getRecsForDatePub.asp?action=Today&incDate=&rad1=des').text
    soup = BeautifulSoup(html, 'lxml')
    data = []
    table = soup.findAll('tr')[3].find('table').find('table')
    rows = table.find_all('tr')
    # http://www2.seattle.gov/fire/realtime911/getRecsForDatePub.asp?incDate=09%2F24%2F16&rad1=des
    previous_day = datetime.date.today()-datetime.timedelta(1)
    previous_day = previous_day.strftime('%m%%2F%d%%2F%y')
    html = requests.get('http://www2.seattle.gov/fire/realtime911/getRecsForDatePub.asp?incDate=%s&rad1=des' % (previous_day)).text
    soup = BeautifulSoup(html, 'lxml')
    data = []
    table = soup.findAll('tr')[3].find('table').find('table')
    rows.extend(table.find_all('tr'))
    for row in rows:
        cols = list(row.findAll('td'))
        incident_id = cols[1].getText()
        db_id = 'SFD_'+incident_id
        existing_data_for_row = existing_data.get(db_id, {})
        is_active = 'class="active"' in str(cols[0])
        if is_active:
            org_address = cols[4].getText()
            address = org_address + ', Seattle'
            address = address.replace('/', '&')
            incident = {'id': db_id, 'agency': 'SFD', 'incident_id': incident_id, 'address': address, 'is_active': is_active, 'unit_timestamps': get_unit_dispatches_for_incident(incident_id)}
            incident["number_of_units_dispatched"] = len(set([row['unit'] for row in incident["unit_timestamps"]]))
            incident["number_of_units_in_service"] = len([row['in_service'] for row in incident["unit_timestamps"] if row['in_service']])
            incident["org_address"] = org_address
            incident["datetime"] = la.localize(dtparse(cols[0].getText()))
            incident["type"] = cols[5].getText()
            incident["streetview_url"] = 'https://maps.googleapis.com/maps/api/streetview?size=100x100&key=AIzaSyB59q3rCxkjqo3K2utcIh0_ju_-URL-L6g&location='+incident['address']
            
            coordinates = addresses_to_coordinates.get(address)
            if coordinates:
                incident["coordinates"] = coordinates
            else:
                coordinates = geocoder.google(address, key='AIzaSyBE-WvY5WPBccBxW-97ZSBCBYEF80NBe7U').latlng
                print coordinates
                incident["coordinates"] = coordinates
            place_name = addresses_to_place_names.get(address)
            if place_name:
                incident["place_name"] = place_name
            else:
                url = 'https://maps.googleapis.com/maps/api/place/nearbysearch/json?location=%s,%s&radius=30.48&key=AIzaSyBE-WvY5WPBccBxW-97ZSBCBYEF80NBe7U' % (incident["coordinates"][0], incident["coordinates"][1])
                print url
                place_name = '; '.join([row.get('name', ' ') for row in requests.get(url).json()['results'][1:]])
                incident["place_name"] = place_name
            assessor_id = addresses_to_assessor_ids.get(address)
            if assessor_id:
                incident["assessor_id"] = assessor_id
                incident["assessor_image_url"] = existing_data_for_row.get('assessor_image_url')
            else:
                url = 'http://gismaps.kingcounty.gov/parcelviewer2/addSearchHandler.ashx?add='+address
                items = requests.get(url).json()['items']
                incident["assessor_id"] = items[0].get('PIN', None) if items else None
                url_beginning = 'http://blue.kingcounty.com/Assessor/eRealProperty/Dashboard.aspx?ParcelNbr='
                url = '%s%s' % (url_beginning, incident["assessor_id"])
                assessor_html = requests.get(url).text
                #print assessor_html
                html_id = 'kingcounty_gov_cphContent_FormViewPictCurr_CurrentImage'
                image_url_beginning = 'http://blue.kingcounty.com/Assessor/eRealProperty/'
                assessor_soup = BeautifulSoup(assessor_html, 'lxml')
                image_url_end = assessor_soup.find(id=html_id)['src']
                image_url = '%s%s' % (image_url_beginning, image_url_end)
                incident["assessor_image_url"] = image_url
            address_history = existing_data_for_row.get('address_history')
            if address_history:
                incident["address_history"] = address_history
            else:
                url = 'https://data.seattle.gov/resource/grwu-wqtk.json?$order=datetime DESC&address='+org_address
                print url
                incident["address_history"] = requests.get(url, verify=False).json()
            data.append(incident)
        else:
            # was it previously active in last loop?
            try:
                if dbtable.get('SFD_'+incident_id).run()['is_active']:
                    dbtable.get('SFD_'+incident_id).update({"is_active": False, "unit_timestamps": get_unit_dispatches_for_incident(incident_id)}).run()
            except:
                exc_type, exc_value, exc_traceback = sys.exc_info()
                print "*** print_tb:"
                traceback.print_tb(exc_traceback, limit=1, file=sys.stdout)
                print "*** print_exception:"
                traceback.print_exception(exc_type, exc_value, exc_traceback,
                                          limit=2, file=sys.stdout)
    return data

def get_unit_dispatches_for_incident(incident_id):
    incident_html = requests.get('http://www2.seattle.gov/fire/IncidentSearch/incidentDetail.asp?ID='+incident_id).text
    incident_soup = BeautifulSoup(incident_html, 'lxml')
    table = incident_soup.findAll('tr')[3].find('table').find('table')
    rows = table.find_all('tr')
    data = []
    for row in rows[1:]:
        cols = list(row.findAll('td'))
        dispatched = cols[1].getText().strip()
        arrived = cols[2].getText().strip()
        in_service = cols[3].getText().strip()
        data.append({'unit': cols[0].getText().strip().strip('*'), 'dispatched': dispatched, 'arrived': arrived, 'in_service': in_service})
    return data

while True:
    print '*'
    try:
        todays_data = get_todays_dispatches()
        #print todays_data
        print table.insert(todays_data).run(conflict='update')
    except:
        exc_type, exc_value, exc_traceback = sys.exc_info()
        print "*** print_tb:"
        traceback.print_tb(exc_traceback, limit=1, file=sys.stdout)
        print "*** print_exception:"
        traceback.print_exception(exc_type, exc_value, exc_traceback,
                                  limit=2, file=sys.stdout)
    time.sleep(5)