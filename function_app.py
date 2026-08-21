import azure.durable_functions as df

from app.activities_blueprint import activities_blueprint
from app.api_blueprint import api_blueprint
from app.durable_blueprint import durable_blueprint


app = df.DFApp()
app.register_blueprint(api_blueprint)
app.register_blueprint(durable_blueprint)
app.register_blueprint(activities_blueprint)