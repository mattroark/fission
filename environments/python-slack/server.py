#!/usr/bin/env python

import logging
import sys
import imp
import os
import bjoern
from gevent.pywsgi import WSGIServer
from flask import Flask, request, abort, g, redirect, url_for
from slackeventsapi import SlackEventAdapter


class FuncApp(Flask):
    def __init__(self, name, loglevel=logging.DEBUG):
        super(FuncApp, self).__init__(name)

        # init the class members
        self.userfunc = None
        self.root = logging.getLogger()
        self.ch = logging.StreamHandler(sys.stdout)

        self.slack_events_adapter = None

        #
        # Logging setup.  TODO: Loglevel hard-coded for now. We could allow
        # functions/routes to override this somehow; or we could create
        # separate dev vs. prod environments.
        #
        self.root.setLevel(loglevel)
        self.ch.setLevel(loglevel)
        self.ch.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(self.ch)

        #
        # Register the routers
        #
        @self.route('/specialize', methods=['POST'])
        def load():
            # load user function from codepath
            codepath = '/userfunc/user'
            self.userfunc = (imp.load_source('user', codepath)).main
            return ""

        @self.route('/v2/specialize', methods=['POST'])
        def loadv2():
            body = request.get_json()
            filepath = body['filepath']
            handler = body['functionName']

            # The value of "functionName" is consist of
            # `<module-name>.<function-name>`.
            moduleName, funcName = handler.split(".")

            # check whether the destination is a directory or a file
            if os.path.isdir(filepath):
                # add package directory path into module search path
                sys.path.append(filepath)

                # find module from package path we append previously.
                # Python will try to find module from the same name
                # file under the package directory. If search is
                # successful, the return value is a 3-element tuple;
                # otherwise, an exception "ImportError" is raised.
                # Second parameter of find_module enforces python to
                # find same name module from the given list of
                # directories to prevent name confliction with
                # built-in modules.
                f, path, desc = imp.find_module(moduleName, [filepath])

                # load module
                # Return module object is the load is successful;
                # otherwise, an exception is raised.
                try:
                    mod = imp.load_module(moduleName, f, path, desc)
                finally:
                    if f:
                        f.close()
            else:
                # load source from destination python file
                mod = imp.load_source(moduleName, filepath)

            # load user function from module
            self.userfunc = getattr(mod, funcName)

            self.slack_events_adapter = _slack_events_adapter()

            return ""

        @self.route('/healthz', methods=['GET'])
        def healthz():
            return "", 200

        def _slack_events_adapter():
            """ Initializes the SlackEventAdapter which will register the specified endpoint as a Flask route. """

            slack_secret_key = 'SLACK_SIGNING_SECRET'
            try:
                path = "/secrets/fission/slack/%s" % slack_secret_key
                sf = open(path, "r")
                os.environ[slack_secret_key] = sf.read()
            except FileNotFoundError:
                logging.error("%s could not be found..." % slack_secret_key)
                abort(500)

            adapter = SlackEventAdapter(signing_secret=os.environ[slack_secret_key], endpoint="/", server=self)

            @adapter.on("app_mention")
            @adapter.on("message")
            def process_event(event_data):
                self.userfunc(event_data)

            return adapter


app = FuncApp(__name__, logging.DEBUG)
slack_events_adapter = app.slack_events_adapter

#
# TODO: this starts the built-in server, which isn't the most
# efficient.  We should use something better.
#
if os.environ.get("WSGI_FRAMEWORK") == "GEVENT":
    app.logger.info("Starting gevent based server")
    svc = WSGIServer(('0.0.0.0', 8888), app)
    svc.serve_forever()
else:
    app.logger.info("Starting bjoern based server")
    bjoern.run(app, '0.0.0.0', 8888, reuse_port=True)
