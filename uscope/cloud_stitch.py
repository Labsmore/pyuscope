import os
import io
import boto3
from uscope import config
from uscope.util import writej
import datetime
import json


class CSInfo:
    def __init__(self,
                 access_key=None,
                 secret_key=None,
                 id_key=None,
                 notification_email=None):
        bc = config.get_bc()
        if not access_key:
            access_key = bc.labsmore_stitch_aws_access_key()
        if not secret_key:
            secret_key = bc.labsmore_stitch_aws_secret_key()
        if not id_key:
            id_key = bc.labsmore_stitch_aws_id_key()
        if not notification_email:
            notification_email = bc.labsmore_stitch_notification_email()

        self._access_key = access_key
        self._secret_key = secret_key
        self._id_key = id_key
        self._notification_email = notification_email

    def is_plausible(self):
        if not self._access_key:
            raise ValueError("Requires access_key")
        if not self._secret_key:
            raise ValueError("Requires secret_key")
        if not self._id_key:
            raise ValueError("Requires id_key")
        if not self._notification_email:
            raise ValueError("Requires notification_email")

    def access_key(self, required=True):
        if required and not self._access_key:
            raise ValueError("Requires access_key")
        return self._access_key

    def secret_key(self, required=True):
        if required and not self._secret_key:
            raise ValueError("Requires secret_key")
        return self._secret_key

    def id_key(self, required=True):
        if required and not self._id_key:
            raise ValueError("Requires id_key")
        return self._id_key

    def notification_email(self, required=True):
        if required and not self._notification_email:
            raise ValueError("Requires notification_email")
        return self._notification_email


def upload_dir(directory,
               verbose=True,
               log=None,
               cs_info=None,
               running=None,
               dst_basename=None):

    if not cs_info:
        cs_info = CSInfo()
    cs_info.is_plausible()

    if not os.path.isdir(directory):
        raise ValueError("Need a directory")

    if dst_basename is None:
        dst_basename = os.path.basename(os.path.abspath(directory))

    if log is None:

        def log(s):
            print(s)

    log(f"CloudStitch uploading: {directory}")
    time_start = datetime.datetime.utcnow().isoformat()

    S3BUCKET = 'labsmore-mosaic-service'
    DEST_DIR = cs_info.id_key() + '/' + dst_basename
    s3 = boto3.client('s3',
                      aws_access_key_id=cs_info.access_key(),
                      aws_secret_access_key=cs_info.secret_key())

    for root, _, files in os.walk(directory):
        for file in sorted(files):
            if running is not None and not running.is_set():
                raise Exception("Upload interrupted")
            verbose and log('Uploading {} to {}/{} '.format(
                os.path.join(root, file), S3BUCKET, DEST_DIR + '/' + file))
            s3.upload_file(os.path.join(root, file), S3BUCKET,
                           DEST_DIR + '/' + file)

    serverj = {
        "email": cs_info.notification_email(),
    }
    bc = config.get_bc()
    if bc.labsmore_stitch_use_xyfstitch():
        serverj["task"] = "mosaic_xyf_stitch"
        serverj["container"] = "mosaic_xyf_stitcher"

    if bc.labsmore_stitch_save_cloudshare():
        serverj["cloudshare"] = "true"

    MOSAIC_RUN_CONTENT = json.dumps(serverj)
    print("up", MOSAIC_RUN_CONTENT)
    mosaic_run_json = io.BytesIO(bytes(MOSAIC_RUN_CONTENT, encoding='utf8'))
    s3.upload_fileobj(mosaic_run_json, S3BUCKET,
                      DEST_DIR + '/' + 'mosaic_run.json')

    time_end = datetime.datetime.utcnow().isoformat()
    log("CloudStitch uploaded")

    outj = {
        "type": "cloud_stitch",
        "time_start": time_start,
        "time_end": time_end,
        "s3_bucket": S3BUCKET,
        "s3_dir": DEST_DIR,
        # maybe? ie log the operator
        "notification_email": cs_info.notification_email(),
    }
    writej(os.path.join(directory, "cloud_stitch.json"), outj)
