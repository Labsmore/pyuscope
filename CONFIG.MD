# .pyuscope

Info on ~/.pyuscope file

## CloudStitch

Please email support@labsmore.com for credentials

    {
        "labsmore_stitch": {
            "aws_access_key": "XXX",
            "aws_secret_key": "XXX",
            "aws_id_key": "XXX",
            "notification_email": "you@example.com",
            //Optional / experimental
            //"use_xyfstitch": true,
        }
    }

## Write local image viewer

    {
        "ipp": {
            "write_html_viewer": true,
            "write_summary_image": true,
        }
    }

## More script directories

    {
        "script_dirs":{
            "rhodium": "/home/labsmore/pyuscope-rhodium/script",
            "kitchen": "/home/labsmore/pyuscope-kitchen/script",
        },
    }

## Turn down system velocity / acceleration

This is useful if you have samples that aren't bolted down well

    {
        "systems": [
            {
                "microscope": "lip-x1",
                "dconfig": {
                    //Keep imager config the same except replace this one key
                    "motion:$": {
                        //Half max velocity, half max acceleration
                        "damper": 0.5
                    },
                }
            }
        ]
    }

## Custom joystick configuration

    {
        "joysticks": [
            {
                "guid": "030032a18e060000ca00000011010000",
                "dconfig": {
                    "axis_move_z": {
                        "args": {
                            "id": 2,
                        },
                        "threshold": 0.1,
                        "scalar": 1.0
                    }
                }
            }
        ]
    }

