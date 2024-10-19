from flask import Blueprint

from opentakserver.blueprints.marti_api.certificate_enrollment_api import certificate_authority_api_blueprint
from opentakserver.blueprints.marti_api.citrap_api import citrap_api_blueprint
from opentakserver.blueprints.marti_api.cot_marti_api import cot_marti_api
from opentakserver.blueprints.marti_api.data_package_marti_api import data_package_marti_api
from opentakserver.blueprints.marti_api.datasync_marti_api import datasync_api
from opentakserver.blueprints.marti_api.device_profile_marti_api import device_profile_marti_api_blueprint
from opentakserver.blueprints.marti_api.group_marti_api import group_api
from opentakserver.blueprints.marti_api.marti_api import marti_api
from opentakserver.blueprints.marti_api.contacts_marti_api import contacts_api

marti_blueprint = Blueprint("marti_blueprint", __name__)

marti_blueprint.register_blueprint(data_package_marti_api)
marti_blueprint.register_blueprint(datasync_api)
marti_blueprint.register_blueprint(cot_marti_api)
marti_blueprint.register_blueprint(device_profile_marti_api_blueprint)
marti_blueprint.register_blueprint(citrap_api_blueprint)
marti_blueprint.register_blueprint(certificate_authority_api_blueprint)
marti_blueprint.register_blueprint(group_api)
marti_blueprint.register_blueprint(marti_api)
marti_blueprint.register_blueprint(contacts_api)
