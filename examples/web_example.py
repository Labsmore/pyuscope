import requests


class PyuscopeHTTPClient:
    def __init__(self, host=None, port=None):
        if host is None:
            host = "localhost"
        self.host = host
        if port is None:
            port = 8080
        self.port = port
        self.base_url = f"http://{self.host}:{self.port}"

    def request(self, page, query_args={}):
        query_str = ""
        if len(query_args):
            query_str = "?" + "&".join(f"{k}={v}"
                                       for k, v in query_args.items())
        response = requests.get(self.base_url + page + query_str)
        return response.json()

    def get_position(self):
        ret = self.request("/get/position")
        pos = ret["data"]
        for k, v in pos.items():
            pos[k] = float(v)
        return pos

    def move_absolute(self, pos, block=True):
        qargs = {"block": int(block)}
        for k, v in pos.items():
            qargs["axis." + k] = v
        print("move_absolute", qargs)
        self.request("/run/move_absolute", qargs)

    def move_relative(self, pos, block=True):
        qargs = {"block": int(block)}
        for k, v in pos.items():
            qargs["axis." + k] = v
        self.request("/run/move_relative", pos, qargs)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Generate calibreation files from specially captured frames'
    )
    parser.add_argument("--host", default="localhost")
    parser.add_argument('--port', default=8080)
    args = parser.parse_args()

    client = PyuscopeHTTPClient(host=args.host, port=args.port)

    pos = client.get_position()
    print(f"Initial position: {pos}")
    delta = 1.0
    print("Moving right")
    pos["x"] += delta
    client.move_absolute(pos)
    pos = client.get_position()
    print(f"Middle position: {pos}")
    print("Moving left")
    pos["x"] -= delta
    client.move_absolute(pos)
    pos = client.get_position()
    print(f"Final position: {pos}")


if __name__ == "__main__":
    main()
