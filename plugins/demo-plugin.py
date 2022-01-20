from http import HTTPStatus
from json import dumps, loads
from tempfile import SpooledTemporaryFile
from typing import Mapping, Optional, List, Dict, Tuple

import marshmallow as ma
from celery.canvas import chain
from celery.utils.log import get_task_logger
from flask import redirect
from flask.wrappers import Response
from flask.app import Flask
from flask.globals import request
from flask.helpers import url_for
from flask.templating import render_template
from flask.views import MethodView
from marshmallow import EXCLUDE

from qhana_plugin_runner.api.plugin_schemas import (
    DataMetadata,
    PluginMetadataSchema,
    PluginMetadata,
    PluginType,
    EntryPoint,
)
from qhana_plugin_runner.api.util import FrontendFormBaseSchema, SecurityBlueprint
from qhana_plugin_runner.celery import CELERY
from qhana_plugin_runner.db.models.tasks import ProcessingTask
from qhana_plugin_runner.storage import STORE
from qhana_plugin_runner.tasks import save_task_error, save_task_result
from qhana_plugin_runner.util.plugins import QHAnaPluginBase, plugin_identifier

import math

from qiskit import QuantumCircuit
from qiskit.circuit import Parameter
from qiskit.opflow import I, X, Y, Z, StateFn, PauliExpectation, OperatorBase

import numpy as np

_plugin_name = "demo-plugin"
__version__ = "v0.1.0"
_identifier = plugin_identifier(_plugin_name, __version__)


DEMO_BLP = SecurityBlueprint(
    _identifier,  # blueprint name
    __name__,  # module import name!
    description="Demo plugin API.",
)

# Input parameters of the plugin
class DemoPluginParametersSchema(FrontendFormBaseSchema):
    target_value = ma.fields.Float(
        required=True,
        allow_none=False,
        metadata={
            "label": "Target Value (between 0 and 1)",
            "description": "A value between 0 and 1. The QNN will be trained to output that value.",
        },
    )


@DEMO_BLP.route("/")
class PluginView(MethodView):
    """Root resource of this plugin."""

    @DEMO_BLP.response(HTTPStatus.OK, PluginMetadataSchema())
    @DEMO_BLP.require_jwt("jwt", optional=True)
    def get(self):
        """Endpoint returning the plugin metadata."""
        return PluginMetadata(
            title=DemoPlugin.instance.name,
            description=DEMO_BLP.description,
            name=DemoPlugin.instance.identifier,
            version=DemoPlugin.instance.version,
            type=PluginType.simple,
            entry_point=EntryPoint(
                href="./process/",
                ui_href="./ui/",
                data_input=[],
                data_output=[
                    DataMetadata(
                        data_type="txt",
                        content_type=["text/plain"],
                        required=True,
                    )
                ],
            ),
            tags=[],
        )


@DEMO_BLP.route("/ui/")
class MicroFrontend(MethodView):
    """Micro frontend for the demo plugin."""

    @DEMO_BLP.html_response(
        HTTPStatus.OK, description="Micro frontend of the demo plugin."
    )
    @DEMO_BLP.arguments(
        DemoPluginParametersSchema(
            partial=True, unknown=EXCLUDE, validate_errors_as_result=True
        ),
        location="query",
        required=False,
    )
    @DEMO_BLP.require_jwt("jwt", optional=True)
    def get(self, errors):
        """Return the micro frontend."""
        return self.render(request.args, errors)

    @DEMO_BLP.html_response(
        HTTPStatus.OK, description="Micro frontend of the hello world plugin."
    )
    @DEMO_BLP.arguments(
        DemoPluginParametersSchema(
            partial=True, unknown=EXCLUDE, validate_errors_as_result=True
        ),
        location="form",
        required=False,
    )
    @DEMO_BLP.require_jwt("jwt", optional=True)
    def post(self, errors):
        """Return the micro frontend with prerendered inputs."""
        return self.render(request.form, errors)

    def render(self, data: Mapping, errors: dict):
        schema = DemoPluginParametersSchema()
        if data.get("targetValue") is None:
            # add default value
            data = {"targetValue": 0.7}
        return Response(
            render_template(
                "simple_template.html",
                name=DemoPlugin.instance.name,
                version=DemoPlugin.instance.version,
                schema=schema,
                values=data,
                errors=errors,
                process=url_for(f"{DEMO_BLP.name}.ProcessView"),
                example_values=url_for(f"{DEMO_BLP.name}.MicroFrontend"),
            )
        )


@DEMO_BLP.route("/process/")
class ProcessView(MethodView):
    """Start a long running processing task."""

    @DEMO_BLP.arguments(DemoPluginParametersSchema(unknown=EXCLUDE), location="form")
    @DEMO_BLP.response(HTTPStatus.SEE_OTHER)
    @DEMO_BLP.require_jwt("jwt", optional=True)
    def post(self, arguments):
        """Start the demo task."""
        db_task = ProcessingTask(task_name=demo_task.name, parameters=dumps(arguments))
        db_task.save(commit=True)

        # all tasks need to know about db id to load the db entry
        task: chain = demo_task.s(db_id=db_task.id) | save_task_result.s(
            db_id=db_task.id
        )
        # save errors to db
        task.link_error(save_task_error.s(db_id=db_task.id))
        task.apply_async()

        db_task.save(commit=True)

        return redirect(
            url_for("tasks-api.TaskView", task_id=str(db_task.id)), HTTPStatus.SEE_OTHER
        )


class DemoPlugin(QHAnaPluginBase):

    name = _plugin_name
    version = __version__

    def __init__(self, app: Optional[Flask]) -> None:
        super().__init__(app)

    def get_api_blueprint(self):
        return DEMO_BLP


TASK_LOGGER = get_task_logger(__name__)


@CELERY.task(name=f"{DemoPlugin.instance.identifier}.demo_task", bind=True)
def demo_task(self, db_id: int) -> str:
    TASK_LOGGER.info(f"Starting new demo task with db id '{db_id}'")
    task_data: Optional[ProcessingTask] = ProcessingTask.get_by_id(id_=db_id)

    if task_data is None:
        msg = f"Could not load task data with id {db_id} to read parameters!"
        TASK_LOGGER.error(msg)
        raise KeyError(msg)

    target_value: Optional[float] = loads(task_data.parameters or "{}").get(
        "target_value", None  # FIXME
    )
    TASK_LOGGER.info(f"Loaded input parameters from db: input_str='{target_value}'")
    if target_value is None:
        target_value = 0.7
    trained_circuit, parameters = qnn(target_value)
    # hacky patch for qiskit circuit drawer to work when celery replaces stdout
    import sys

    sys.stdout.encoding = "utf8"
    # hacky patch end
    out_str = str(trained_circuit.draw("text"))
    output_quasm = trained_circuit.bind_parameters(parameters).qasm()
    if output_quasm is None:
        TASK_LOGGER.warning("Could not export circuit as openqasm program.")
    with SpooledTemporaryFile(mode="w") as output:
        output.write(out_str)
        output.write(
            "\n\nWith parameters:\n"
            + "\n".join(f"{p}: {v}" for p, v in parameters.items())
            + f"\n\nTraining target: {target_value}"
        )
        STORE.persist_task_result(
            db_id, output, "trained-circuit.txt", "trained-circuit", "text/plain"
        )
    if output_quasm:
        with SpooledTemporaryFile(mode="w") as output:
            output.write(output_quasm)
            STORE.persist_task_result(
                db_id,
                output,
                "trained-circuit.qasm",
                "trained-circuit.qasm",
                "text/x-qasm",
            )
    return "result: " + repr(parameters)


################################################################################
### Implement the method below this line #######################################
################################################################################


def qnn(target_value: float) -> Tuple[QuantumCircuit, Dict[Parameter, float]]:
    pass
