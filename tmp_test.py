from Webapp import app
with app.test_request_context('/api/state'):
    resp = app.full_dispatch_request()
    print('state', resp.status_code, resp.get_json())

with app.test_request_context('/api/new', method='POST', json={'mode':'IA','difficulty':'easy','starting_player':'R','client_id':'X'}):
    resp = app.full_dispatch_request()
    print('new', resp.status_code, resp.get_json())
