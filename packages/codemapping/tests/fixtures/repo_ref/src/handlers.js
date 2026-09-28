const routes = {
  "/health": function (req, res) {
    res.status(200).json({ status: "ok" });
  },
  "/users": (req, res) => {
    res.json({ users: [] });
  },
};

module.exports = routes;